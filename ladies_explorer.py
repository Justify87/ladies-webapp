#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

VERSION = "9.2.1"
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_FILE = SCRIPT_DIR / "pyproject.toml"
NODE_VERSION = "22.23.3"
NODE_MIN_MAJOR = 20
NODE_MAX_MAJOR = 24
NPM_MIN_MAJOR = 10
BOOTSTRAP_MARKER = "LADIES_BOOTSTRAP_CHILD"
PYTHON_MIN = (3, 12)
PYTHON_MAX_EXCLUSIVE = (3, 14)
PYTHON_REQUIREMENTS = {
    "Flask": ("flask", (3, 1), (4, 0)),
    "requests": ("requests", (2, 32), (3, 0)),
    "beautifulsoup4": ("bs4", (4, 12), (5, 0)),
    "Pillow": ("PIL", (10, 0), (15, 0)),
    "SQLAlchemy": ("sqlalchemy", (2, 0), (3, 0)),
    "orjson": ("orjson", (3, 10), (4, 0)),
    "qrcode": ("qrcode", (8, 0), (9, 0)),
    "nodeenv": ("nodeenv", (1, 10), (2, 0)),
}


def _in_virtualenv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix) or bool(os.environ.get("VIRTUAL_ENV"))


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = [int(x) for x in re.findall(r"\d+", value)[:3]]
    return tuple((parts + [0, 0, 0])[:3])  # type: ignore[return-value]


def _python_version_supported() -> bool:
    current = sys.version_info[:2]
    return PYTHON_MIN <= current < PYTHON_MAX_EXCLUSIVE


def _project_ready() -> bool:
    if not _python_version_supported() or importlib.util.find_spec("ladies_v9") is None:
        return False
    for dist_name, (module_name, minimum, maximum) in PYTHON_REQUIREMENTS.items():
        if importlib.util.find_spec(module_name) is None:
            return False
        try:
            current = _version_tuple(importlib.metadata.version(dist_name))
        except importlib.metadata.PackageNotFoundError:
            return False
        if not (_version_tuple(".".join(map(str, minimum))) <= current < _version_tuple(".".join(map(str, maximum)))):
            return False
    return True


def _venv_python(root: Path) -> Path:
    if os.name == "nt":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def _reexec(executable: str | Path, argv: list[str], *, mark_child: bool = True) -> None:
    env = os.environ.copy()
    if mark_child:
        env[BOOTSTRAP_MARKER] = "1"
    os.execve(str(executable), [str(executable), *argv], env)


def _uv_reexec(uv: str) -> None:
    print("[Setup] Python-Umgebung wird mit uv synchronisiert …")
    _reexec(
        uv,
        ["run", "--project", str(SCRIPT_DIR), "python", str(Path(__file__).resolve()), *sys.argv[1:]],
    )


def ensure_python_environment() -> None:
    """Install into the exact invoked environment, or create a local one safely.

    - `uv run` already syncs pyproject dependencies before this file starts.
    - direct Python inside a supported venv is repaired in-place.
    - direct system Python is never mutated: use uv's project env when available,
      otherwise create .venv and re-exec there.
    - unsupported Python versions are handed to uv when available instead of
      trying to mutate or upgrade the system interpreter.
    """
    if _project_ready():
        return
    if not PROJECT_FILE.exists():
        raise RuntimeError(f"Projektdefinition fehlt: {PROJECT_FILE}")

    uv = shutil.which("uv")
    if not _python_version_supported():
        if uv and os.environ.get(BOOTSTRAP_MARKER) != "1":
            _uv_reexec(uv)
        raise RuntimeError(
            f"Python 3.12 oder 3.13 erforderlich; aktiv ist {sys.version.split()[0]}. "
            "Installiere eine passende Python-Version oder uv."
        )

    if not _in_virtualenv() and os.environ.get(BOOTSTRAP_MARKER) != "1":
        if uv:
            _uv_reexec(uv)
        venv_dir = SCRIPT_DIR / ".venv"
        python = _venv_python(venv_dir)
        if not python.exists():
            print("[Setup] Lokale Python-Umgebung .venv wird angelegt …")
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        print("[Setup] Python-Abhängigkeiten werden in .venv installiert …")
        subprocess.run([str(python), "-m", "pip", "install", "-e", str(SCRIPT_DIR)], check=True)
        _reexec(python, [str(Path(__file__).resolve()), *sys.argv[1:]])

    print(f"[Setup] Python-Abhängigkeiten werden für {sys.executable} synchronisiert …")
    if uv:
        subprocess.run(
            [uv, "pip", "install", "--python", sys.executable, "-e", str(SCRIPT_DIR)],
            check=True,
        )
    else:
        try:
            subprocess.run([sys.executable, "-m", "pip", "--version"], check=True, stdout=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"], check=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "-e", str(SCRIPT_DIR)], check=True)

    if not _project_ready():
        raise RuntimeError("Python-Abhängigkeiten konnten nicht vollständig in der aktiven Umgebung eingerichtet werden")


def _major(executable: str, expression: list[str]) -> int | None:
    try:
        value = subprocess.check_output([executable, *expression], text=True, timeout=8).strip()
        return int(value.split(".", 1)[0].lstrip("v"))
    except Exception:
        return None


def _node_pair_ok(node: str | None, npm: str | None) -> bool:
    if not node or not npm:
        return False
    node_major = _major(node, ["-p", "process.versions.node"])
    npm_major = _major(npm, ["--version"])
    return (
        node_major is not None
        and NODE_MIN_MAJOR <= node_major <= NODE_MAX_MAJOR
        and npm_major is not None
        and npm_major >= NPM_MIN_MAJOR
    )


def _local_node_bin(runtime_dir: Path) -> Path:
    return runtime_dir / ("Scripts" if os.name == "nt" else "bin")


def _prepend_path(path: Path) -> None:
    current = os.environ.get("PATH", "")
    value = str(path)
    if value not in current.split(os.pathsep):
        os.environ["PATH"] = value + os.pathsep + current


def ensure_node_runtime() -> tuple[str, str]:
    """Use a compatible system Node/npm or provision a project-local runtime."""
    node, npm = shutil.which("node"), shutil.which("npm")
    if _node_pair_ok(node, npm):
        return str(node), str(npm)

    runtime_dir = SCRIPT_DIR / ".runtime" / f"node-{NODE_VERSION}"
    bin_dir = _local_node_bin(runtime_dir)
    _prepend_path(bin_dir)
    node, npm = shutil.which("node"), shutil.which("npm")
    if _node_pair_ok(node, npm):
        return str(node), str(npm)

    print(f"[Setup] Node.js {NODE_VERSION} + npm werden projektlokal installiert …")
    runtime_dir.parent.mkdir(parents=True, exist_ok=True)
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir, ignore_errors=True)
    subprocess.run(
        [sys.executable, "-m", "nodeenv", str(runtime_dir), "--node", NODE_VERSION, "--prebuilt"],
        check=True,
    )
    _prepend_path(bin_dir)
    node, npm = shutil.which("node"), shutil.which("npm")
    if not _node_pair_ok(node, npm):
        raise RuntimeError("Projektlokale Node.js-/npm-Runtime konnte nicht eingerichtet werden")
    return str(node), str(npm)


def ensure_javascript_dependencies(npm: str) -> None:
    worker_dir = SCRIPT_DIR / "ladies" / "messaging" / "whatsapp_worker"
    lock = worker_dir / "package-lock.json"
    if not lock.exists():
        raise RuntimeError(f"WhatsApp-Lockfile fehlt: {lock}")
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    modules = worker_dir / "node_modules" / "@whiskeysockets" / "baileys"
    stamp = worker_dir / "node_modules" / ".ladies-lock.sha256"
    if modules.exists() and stamp.exists():
        try:
            if stamp.read_text(encoding="ascii").strip() == digest:
                return
        except OSError:
            pass
    print("[Setup] WhatsApp-Abhängigkeiten werden aus package-lock.json installiert …")
    subprocess.run(
        [npm, "ci", "--omit=dev", "--no-audit", "--no-fund"],
        cwd=worker_dir,
        check=True,
    )
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(digest + "\n", encoding="ascii")


def migrate_legacy_paths() -> None:
    """One-time rename of local versioned defaults without losing user data."""
    old_db = SCRIPT_DIR / "ladies_v9.sqlite3"
    new_db = SCRIPT_DIR / "ladies.sqlite3"
    if old_db.exists() and not new_db.exists():
        old_db.replace(new_db)
        for suffix in ("-wal", "-shm"):
            old_sidecar = Path(str(old_db) + suffix)
            if old_sidecar.exists():
                old_sidecar.replace(Path(str(new_db) + suffix))
        print("[Migration] Datenbank auf ladies.sqlite3 umbenannt.")

    old_data = SCRIPT_DIR / "ladies_v9_data"
    new_data = SCRIPT_DIR / "ladies_data"
    if old_data.exists() and not new_data.exists():
        old_data.replace(new_data)
        print("[Migration] Datenverzeichnis auf ladies_data umbenannt.")


def configure_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ladies_explorer")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s [%(threadName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler = RotatingFileHandler(log_path, maxBytes=10_000_000, backupCount=7, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(formatter)
        logger.addHandler(console)

    def _uncaught(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            return sys.__excepthook__(exc_type, exc_value, exc_tb)
        logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _uncaught
    logger.info("Logging initialized file=%s python=%s", log_path.resolve(), sys.version.split()[0])
    return logger


def local_ipv4_addresses() -> list[str]:
    out: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in out:
                out.append(ip)
    except OSError:
        pass
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if not ip.startswith("127.") and ip not in out:
            out.insert(0, ip)
    except OSError:
        pass
    return out


def main() -> int:
    ensure_python_environment()
    migrate_legacy_paths()
    _, npm = ensure_node_runtime()
    ensure_javascript_dependencies(npm)

    from ladies_v9.app import create_app

    parser = argparse.ArgumentParser(description=f"Ladies Explorer V{VERSION}")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--city", default="leipzig")
    parser.add_argument("--radius", type=int, default=20)
    parser.add_argument("--profile-delay", type=float, default=2.0, help="Mindestabstand zwischen vollständigen Profilabrufen")
    parser.add_argument("--db", default=str(SCRIPT_DIR / "ladies.sqlite3"))
    parser.add_argument("--data-dir", default=str(SCRIPT_DIR / "ladies_data"))
    parser.add_argument("--log", default=str(SCRIPT_DIR / "logs" / "ladies_explorer.log"), help="Debug-Logdatei")
    args = parser.parse_args()

    logger = configure_logging(Path(args.log))
    logger.info(
        "Starting Ladies Explorer version=%s host=%s port=%s db=%s data_dir=%s",
        VERSION,
        args.host,
        args.port,
        Path(args.db).resolve(),
        Path(args.data_dir).resolve(),
    )
    app = create_app(
        db_path=args.db,
        data_dir=args.data_dir,
        default_city=args.city,
        default_radius=max(1, args.radius),
        profile_delay=max(1.0, args.profile_delay),
    )

    print(f"Ladies Explorer V{VERSION}")
    print(f"Datenbank: {Path(args.db).resolve()}")
    print(f"Archiv:    {Path(args.data_dir).resolve()}")
    print(f"Debug-Log: {Path(args.log).resolve()}")
    print(f"Lokal:     http://127.0.0.1:{args.port}")
    if args.host in {"0.0.0.0", "::"}:
        token = app.config.get("LAN_ACCESS_TOKEN")
        for ip in local_ipv4_addresses():
            if token:
                print(f"Heimnetz-Pairing: http://{ip}:{args.port}/pair/{token}")
            else:
                print(f"Heimnetz:  http://{ip}:{args.port}")
        print("Hinweis: Im Heimnetz zuerst einmalig die Pairing-URL auf dem Smartphone öffnen.")
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
