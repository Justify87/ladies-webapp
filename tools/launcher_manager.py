#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import deque
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

try:
    from tools import portable_manager as pm
except ImportError:
    install_root = Path(__file__).resolve().parent.parent
    current = (install_root / "current").read_text(encoding="utf-8").strip()
    sys.path.insert(0, str(install_root / "releases" / current))
    from tools import portable_manager as pm

MANAGER_HOST = "127.0.0.1"
APP_HOST = "0.0.0.0"
APP_PORT = 8765
READY_TIMEOUT = 45.0
STOP_TIMEOUT = 10.0
MAX_BACKUPS = 3
MANAGER_STATE_FILE = "manager.json"
MANAGER_COPY = "launcher_manager.py"
MANAGER_UI = "launcher_manager.html"
MANAGER_LOG = "manager.log"
APP_CONSOLE_LOG = "app-console.log"
LOGGER = logging.getLogger("ladies_launcher_manager")


def _state_dir(root: Path) -> Path:
    path = root / pm.STATE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manager_state_path(root: Path) -> Path:
    return _state_dir(root) / MANAGER_STATE_FILE


def _manager_copy_path(root: Path) -> Path:
    return _state_dir(root) / MANAGER_COPY


def _manager_ui_path(root: Path) -> Path:
    return _state_dir(root) / MANAGER_UI


def _app_url() -> str:
    return f"http://127.0.0.1:{APP_PORT}/"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _venv_python(release: Path) -> Path:
    return release / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _copy_manager_to_state(root: Path, release: Path) -> Path:
    state = _state_dir(root)
    pairs = (
        (release / "tools" / MANAGER_COPY, state / MANAGER_COPY),
        (release / "tools" / MANAGER_UI, state / MANAGER_UI),
    )
    for source, target in pairs:
        if not source.is_file():
            if target.is_file():
                continue
            raise pm.PortableError(f"Launcher-Manager-Datei fehlt: {source}")
        temporary = target.with_suffix(target.suffix + ".tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    return state / MANAGER_COPY


def _read_manager_state(root: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(_manager_state_path(root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _manager_url(state: dict[str, Any]) -> str:
    token = urllib.parse.quote(str(state["token"]), safe="")
    return f"http://{MANAGER_HOST}:{int(state['port'])}/?token={token}"


def _manager_alive(state: dict[str, Any]) -> bool:
    try:
        pid, port, token = int(state["pid"]), int(state["port"]), str(state["token"])
    except (KeyError, TypeError, ValueError):
        return False
    if not pm._process_running(pid):
        return False
    request = urllib.request.Request(
        f"http://{MANAGER_HOST}:{port}/api/ping",
        headers={"X-Ladies-Manager-Token": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _configure_logging(root: Path) -> None:
    if LOGGER.handlers:
        return
    LOGGER.setLevel(logging.INFO)
    handler = logging.FileHandler(_state_dir(root) / MANAGER_LOG, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.addHandler(handler)


def _spawn_detached(command: list[str], *, cwd: Path, log_path: Path) -> None:
    stream = log_path.open("a", encoding="utf-8")
    kwargs: dict[str, Any] = dict(
        cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, close_fds=True
    )
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(command, **kwargs)
    finally:
        stream.close()


def launch_manager(root: Path, *, open_browser: bool = True) -> int:
    root = root.resolve()
    state = _read_manager_state(root)
    if state and _manager_alive(state):
        if open_browser:
            webbrowser.open(_manager_url(state), new=2)
        return 0

    current = pm.read_current(root)
    release = root / pm.RELEASES_DIR / current
    pm.validate_release_dir(release, current)
    manager = _copy_manager_to_state(root, release)
    try:
        _manager_state_path(root).unlink()
    except FileNotFoundError:
        pass
    _spawn_detached(
        [sys.executable, str(manager), "serve", "--root", str(root)],
        cwd=root,
        log_path=_state_dir(root) / MANAGER_LOG,
    )
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        state = _read_manager_state(root)
        if state and _manager_alive(state):
            if open_browser:
                webbrowser.open(_manager_url(state), new=2)
            return 0
        time.sleep(0.15)
    raise pm.PortableError("Launcher Manager konnte nicht gestartet werden. Siehe .state/manager.log")


def ensure_release_environment(root: Path, tag: str, emit: Callable[[str], None] | None = None) -> Path:
    release = root / pm.RELEASES_DIR / pm._tag(tag)
    pm.validate_release_dir(release, tag)
    python = _venv_python(release)
    if python.is_file():
        return python
    uv = shutil.which("uv")
    if not uv:
        raise pm.PortableError("uv ist nicht verfügbar; die Release-Umgebung kann nicht eingerichtet werden")
    if emit:
        emit("Python-Umgebung wird synchronisiert …")
    env = os.environ.copy()
    env["UV_PYTHON_INSTALL_DIR"] = str(root / ".runtime" / "python")
    env["UV_PROJECT_ENVIRONMENT"] = str(release / ".venv")
    Path(env["UV_PYTHON_INSTALL_DIR"]).mkdir(parents=True, exist_ok=True)
    subprocess.run([uv, "python", "install", "3.12"], env=env, check=True, stdout=subprocess.DEVNULL)
    managed_python = subprocess.check_output([uv, "python", "find", "3.12"], env=env, text=True).strip()
    subprocess.run(
        [
            uv, "run", "--locked", "--project", str(release), "--python", managed_python,
            "python", "-c", "import sys; print(sys.executable)",
        ],
        env=env,
        cwd=release,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    if not python.is_file():
        raise pm.PortableError(f"Release-venv wurde nicht angelegt: {python}")
    return python


def _app_command(root: Path, python: Path) -> list[str]:
    current = pm.read_current(root)
    release = root / pm.RELEASES_DIR / current
    db_path, data_dir, log_path = pm._data_paths(root)
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return [
        str(python), str(release / "ladies_explorer.py"),
        "--host", APP_HOST, "--port", str(APP_PORT),
        "--db", str(db_path), "--data-dir", str(data_dir), "--log", str(log_path),
    ]


def _port_available(port: int) -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        try:
            sock.bind((MANAGER_HOST, port))
            return True
        except OSError:
            return False


def _watch_app(process: subprocess.Popen[Any], pid_path: Path) -> None:
    process.wait()
    if pm._read_pid(pid_path) == process.pid:
        try:
            pid_path.unlink()
        except OSError:
            pass


def start_app(root: Path, emit: Callable[[str], None] | None = None, *, open_browser: bool = True) -> int:
    root = root.resolve()
    with pm.lifecycle_lock(root, "start"):
        existing = pm.active_pid(root)
        if existing is not None:
            if open_browser:
                webbrowser.open(_app_url(), new=2)
            return existing
        if not _port_available(APP_PORT):
            raise pm.PortableError(f"Port {APP_PORT} ist bereits belegt")
        current = pm.read_current(root)
        python = ensure_release_environment(root, current, emit)
        console = (_state_dir(root) / APP_CONSOLE_LOG).open("a", encoding="utf-8")
        kwargs: dict[str, Any] = dict(
            cwd=str(root / pm.RELEASES_DIR / current), stdin=subprocess.DEVNULL,
            stdout=console, stderr=subprocess.STDOUT, close_fds=True,
        )
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )
        else:
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(_app_command(root, python), **kwargs)
        finally:
            console.close()
        pm._atomic_write(pm._pid_path(root), f"{process.pid}\n")
        threading.Thread(target=_watch_app, args=(process, pm._pid_path(root)), daemon=True).start()

    if emit:
        emit(f"Ladies Explorer wird gestartet (PID {process.pid}) …")
    deadline = time.monotonic() + READY_TIMEOUT
    request = urllib.request.Request(_app_url() + "api/config")
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise pm.PortableError(f"Ladies Explorer wurde mit Exit-Code {process.returncode} beendet")
        try:
            with urllib.request.urlopen(request, timeout=1.5) as response:
                if response.status == 200:
                    if emit:
                        emit("Ladies Explorer ist bereit.")
                    if open_browser:
                        webbrowser.open(_app_url(), new=2)
                    return process.pid
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    raise pm.PortableError("Ladies Explorer wurde nicht rechtzeitig erreichbar")


def _terminate_pid_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
        )
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def stop_app(root: Path, emit: Callable[[str], None] | None = None) -> bool:
    root = root.resolve()
    with pm.lifecycle_lock(root, "stop"):
        pid = pm.active_pid(root)
        if pid is None:
            return False
        if emit:
            emit(f"Ladies Explorer wird beendet (PID {pid}) …")
        _terminate_pid_tree(pid)
        deadline = time.monotonic() + STOP_TIMEOUT
        while time.monotonic() < deadline and pm._process_running(pid):
            time.sleep(0.15)
        if pm._process_running(pid):
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )
            else:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        try:
            pm._pid_path(root).unlink()
        except OSError:
            pass
        return True


def _backup_database(root: Path) -> Path | None:
    db_path, _, _ = pm._data_paths(root)
    if not db_path.is_file():
        return None
    backup_dir = _state_dir(root) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup = backup_dir / f"pre-update-{stamp}.sqlite3"
    source_uri = f"file:{db_path.as_posix()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True, timeout=30)) as source, closing(
        sqlite3.connect(str(backup))
    ) as target:
        source.backup(target)
    backups = sorted(backup_dir.glob("pre-update-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[MAX_BACKUPS:]:
        try:
            old.unlink()
        except OSError:
            pass
    return backup


def _restore_database(root: Path, backup: Path) -> None:
    db_path, _, _ = pm._data_paths(root)
    for sidecar in (Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass
    with closing(sqlite3.connect(str(backup), timeout=30)) as source, closing(
        sqlite3.connect(str(db_path))
    ) as target:
        source.backup(target)


def migrate_database(root: Path, tag: str, emit: Callable[[str], None] | None = None) -> int:
    python = ensure_release_environment(root, tag, emit)
    db_path, _, _ = pm._data_paths(root)
    if emit:
        emit("Datenbankschema wird geprüft und bei Bedarf migriert …")
    code = (
        "from ladies_v9.database import Database; import sys; "
        "db=Database(sys.argv[1]); db.create_schema(); print(db.schema_version())"
    )
    result = subprocess.check_output(
        [str(python), "-c", code, str(db_path)],
        cwd=root / pm.RELEASES_DIR / pm._tag(tag),
        text=True,
    ).strip()
    return int(result.splitlines()[-1])


def _tail(path: Path, limit: int) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return list(deque((line.rstrip("\n") for line in handle), maxlen=limit))
    except OSError:
        return []


def _open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Controller:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._lock = threading.Lock()
        self._operation: dict[str, Any] = {
            "active": False, "name": None, "message": "Bereit", "error": None,
            "started_at": None, "finished_at": None, "messages": [],
        }
        self._latest: dict[str, Any] | None = None
        self.shutdown_callback: Callable[[], None] | None = None

    def emit(self, message: str) -> None:
        LOGGER.info(message)
        with self._lock:
            messages = [*(self._operation.get("messages") or []), message][-20:]
            self._operation.update(messages=messages, message=message)

    def begin(self, name: str, action: Callable[[], None]) -> bool:
        with self._lock:
            if self._operation["active"]:
                return False
            self._operation = {
                "active": True, "name": name, "message": "Wird vorbereitet …", "error": None,
                "started_at": _now(), "finished_at": None, "messages": [],
            }

        def runner() -> None:
            try:
                action()
            except Exception as exc:
                LOGGER.exception("Operation failed name=%s", name)
                with self._lock:
                    self._operation.update(error=str(exc), message="Fehlgeschlagen")
            finally:
                with self._lock:
                    self._operation.update(active=False, finished_at=_now())

        threading.Thread(target=runner, name=f"manager-{name}", daemon=True).start()
        return True

    def check_update(self) -> None:
        self.emit("Neueste stabile Version wird geprüft …")
        release = pm.latest_release()
        latest, current = pm._tag(str(release.get("tag_name") or "")), pm.read_current(self.root)
        self._latest = {
            "version": latest,
            "available": pm._semver(latest) > pm._semver(current),
            "notes": str(release.get("body") or "").strip(),
            "checked_at": _now(),
        }
        self.emit(f"Installiert: {current} · Neueste Version: {latest}")

    def apply_update(self) -> None:
        release = pm.latest_release()
        latest, previous = pm._tag(str(release.get("tag_name") or "")), pm.read_current(self.root)
        if pm._semver(latest) <= pm._semver(previous):
            self._latest = {
                "version": latest, "available": False, "notes": str(release.get("body") or ""), "checked_at": _now()
            }
            self.emit("Ladies Explorer ist bereits aktuell.")
            return

        was_running = pm.active_pid(self.root) is not None
        if was_running:
            stop_app(self.root, self.emit)
        backup = _backup_database(self.root)
        if backup:
            self.emit(f"Sicherungsstand erstellt: {backup.name}")

        try:
            self.emit(f"Update {previous} → {latest} wird geladen und geprüft …")
            installed = pm.update(self.root)
            _copy_manager_to_state(self.root, self.root / pm.RELEASES_DIR / installed)
            ensure_release_environment(self.root, installed, self.emit)
            schema = migrate_database(self.root, installed, self.emit)
            self.emit(f"Update abgeschlossen. Datenbankschema: {schema}")
            self._latest = {
                "version": installed, "available": False,
                "notes": str(release.get("body") or "").strip(), "checked_at": _now(),
            }
            if was_running:
                start_app(self.root, self.emit, open_browser=True)
        except Exception:
            try:
                if backup and backup.exists():
                    self.emit("Update fehlgeschlagen; Datenbank-Sicherung wird wiederhergestellt …")
                    _restore_database(self.root, backup)
                pm.set_current(self.root, previous)
                self.emit(f"Aktive Version wurde auf {previous} zurückgesetzt.")
                if was_running:
                    start_app(self.root, self.emit, open_browser=True)
            except Exception:
                LOGGER.exception("Update recovery failed")
            raise

    def status(self) -> dict[str, Any]:
        current = pm.read_current(self.root)
        manifest = pm.validate_release_dir(self.root / pm.RELEASES_DIR / current, current)
        pid = pm.active_pid(self.root)
        db_path, data_dir, log_path = pm._data_paths(self.root)
        backups = sorted((_state_dir(self.root) / "backups").glob("pre-update-*.sqlite3"), reverse=True)
        with self._lock:
            operation = json.loads(json.dumps(self._operation))
            latest = json.loads(json.dumps(self._latest)) if self._latest else None
        return {
            "version": current,
            "release_schema": int(manifest["db_schema_version"]),
            "db_schema": pm._db_schema_version(self.root),
            "running": pid is not None,
            "pid": pid,
            "app_url": _app_url(),
            "data_dir": str(data_dir),
            "db_path": str(db_path),
            "last_backup": backups[0].name if backups else None,
            "latest": latest,
            "operation": operation,
            "logs": _tail(log_path, 60),
            "console": _tail(_state_dir(self.root) / APP_CONSOLE_LOG, 30),
            "uv_available": shutil.which("uv") is not None,
        }


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def make_handler(controller: Controller, token: str, ui: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "LadiesLauncher/1"

        def log_message(self, fmt: str, *args: Any) -> None:
            LOGGER.debug(fmt, *args)

        def _authorized(self) -> bool:
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            supplied = self.headers.get("X-Ladies-Manager-Token") or (query.get("token") or [""])[0]
            return bool(supplied) and hmac.compare_digest(str(supplied), token)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            for key, value in (
                ("Content-Type", content_type), ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
                ("Referrer-Policy", "no-referrer"),
                ("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"),
            ):
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, _json_bytes(payload), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorized():
                self._json(403, {"error": "Nicht autorisiert"})
                return
            path = urllib.parse.urlparse(self.path).path
            if path == "/":
                self._send(200, ui.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/ping":
                self._json(200, {"ok": True})
            elif path == "/api/status":
                try:
                    self._json(200, controller.status())
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            elif path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            else:
                self._json(404, {"error": "Nicht gefunden"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                self._json(403, {"error": "Nicht autorisiert"})
                return
            if int(self.headers.get("Content-Length") or 0) > 4096:
                self._json(413, {"error": "Anfrage zu groß"})
                return
            path = urllib.parse.urlparse(self.path).path
            actions: dict[str, tuple[str, Callable[[], None]]] = {
                "/api/app/start": ("start", lambda: start_app(controller.root, controller.emit, open_browser=True)),
                "/api/app/stop": ("stop", lambda: stop_app(controller.root, controller.emit)),
                "/api/update/check": ("check-update", controller.check_update),
                "/api/update/apply": ("update", controller.apply_update),
            }
            if path in actions:
                name, action = actions[path]
                if not controller.begin(name, action):
                    self._json(409, {"error": "Es läuft bereits eine Manager-Aktion"})
                else:
                    self._json(202, {"ok": True})
                return
            if path == "/api/app/open":
                if pm.active_pid(controller.root) is None:
                    self._json(409, {"error": "Ladies Explorer läuft nicht"})
                else:
                    webbrowser.open(_app_url(), new=2)
                    self._json(200, {"ok": True})
                return
            if path == "/api/data/open":
                _, data_dir, _ = pm._data_paths(controller.root)
                data_dir.mkdir(parents=True, exist_ok=True)
                _open_path(data_dir)
                self._json(200, {"ok": True})
                return
            if path == "/api/manager/quit":
                self._json(200, {"ok": True})
                if controller.shutdown_callback:
                    threading.Thread(target=controller.shutdown_callback, daemon=True).start()
                return
            self._json(404, {"error": "Nicht gefunden"})

    return Handler


def serve_manager(root: Path) -> int:
    root = root.resolve()
    _configure_logging(root)
    current = pm.read_current(root)
    pm.validate_release_dir(root / pm.RELEASES_DIR / current, current)
    ui_path = Path(__file__).resolve().with_name(MANAGER_UI)
    if not ui_path.is_file():
        ui_path = root / pm.RELEASES_DIR / current / "tools" / MANAGER_UI
    if not ui_path.is_file():
        raise pm.PortableError(f"Manager-Oberfläche fehlt: {ui_path}")
    token = secrets.token_urlsafe(24)
    controller = Controller(root)
    server = ThreadingHTTPServer((MANAGER_HOST, 0), make_handler(controller, token, ui_path.read_text(encoding="utf-8")))
    controller.shutdown_callback = server.shutdown
    pm._atomic_write(
        _manager_state_path(root),
        json.dumps({"pid": os.getpid(), "port": server.server_port, "token": token, "started_at": _now()}) + "\n",
    )
    LOGGER.info("Launcher Manager ready port=%s current=%s", server.server_port, current)
    try:
        server.serve_forever(poll_interval=0.3)
    finally:
        server.server_close()
        try:
            _manager_state_path(root).unlink()
        except OSError:
            pass
        LOGGER.info("Launcher Manager stopped")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ladies Explorer Launcher Manager")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("launch", "serve"):
        command = sub.add_parser(name)
        command.add_argument("--root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return launch_manager(args.root) if args.command == "launch" else serve_manager(args.root)
    except (pm.PortableError, subprocess.SubprocessError, OSError, ValueError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
