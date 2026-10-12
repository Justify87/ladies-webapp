from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

LOGGER = logging.getLogger("ladies_explorer.messaging.worker")
ALLOWED_STATES = {
    "UNAVAILABLE", "DEPENDENCY_MISSING", "NOT_CONFIGURED", "QR_REQUIRED",
    "PAIRING", "CONNECTING", "CONNECTED", "DISCONNECTED", "RECONNECTING",
    "LOGGED_OUT", "ERROR",
}
RESTART_DELAYS = (1, 2, 5, 10, 30)


def _runtime_executable(name: str) -> str | None:
    """Resolve node/npm from PATH; startup bootstrap may prepend a local runtime."""
    return shutil.which(name)


class BaileysWorkerManager:
    def __init__(self, worker_dir: Path, auth_dir: Path, event_handler: Callable[[dict[str, Any]], None]):
        self.worker_dir = Path(worker_dir)
        self.auth_dir = Path(auth_dir)
        self.event_handler = event_handler
        self.proc: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._pending: dict[str, queue.Queue] = {}
        self._stopping = False
        self._restart_attempt = 0
        self.state = "NOT_CONFIGURED"
        self.detail = None
        self.qr = None
        self.account = None
        self.last_connected_at = None
        atexit.register(self.stop)

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "qr_available": bool(self.qr),
            "account": self.account,
            "last_connected_at": self.last_connected_at,
        }

    def _set_state(self, state: str, detail: str | None = None) -> None:
        state = str(state or "ERROR").upper()
        if state not in ALLOWED_STATES:
            state, detail = "ERROR", "Unbekannter Worker-Zustand"
        self.state = state
        self.detail = detail
        self.event_handler({"type": "connection", "state": state, "detail": detail})

    def dependency_status(self) -> tuple[bool, str | None]:
        node = _runtime_executable("node")
        if not node:
            return False, "Node.js fehlt"
        try:
            major = int(subprocess.check_output(
                [node, "-p", "process.versions.node.split('.')[0]"],
                text=True,
                timeout=5,
            ).strip())
            if major < 20 or major > 24:
                return False, "Node.js 20–24 erforderlich"
        except Exception:
            return False, "Node.js-Version konnte nicht geprüft werden"
        npm = _runtime_executable("npm")
        if not npm:
            return False, "npm fehlt"
        try:
            npm_major = int(subprocess.check_output([npm, "--version"], text=True, timeout=5).strip().split(".", 1)[0])
            if npm_major < 10:
                return False, "npm 10+ erforderlich"
        except Exception:
            return False, "npm-Version konnte nicht geprüft werden"
        return True, None

    def bootstrap(self) -> bool:
        ok, detail = self.dependency_status()
        if not ok:
            self._set_state("DEPENDENCY_MISSING", detail)
            return False
        npm = _runtime_executable("npm")
        if not npm:
            self._set_state("DEPENDENCY_MISSING", "npm fehlt")
            return False
        lock = self.worker_dir / "package-lock.json"
        if not lock.exists():
            self._set_state("UNAVAILABLE", "WhatsApp-Lockfile fehlt")
            return False
        lock_digest = hashlib.sha256(lock.read_bytes()).hexdigest()
        stamp = self.worker_dir / "node_modules" / ".ladies-lock.sha256"
        modules = self.worker_dir / "node_modules" / "@whiskeysockets" / "baileys"
        try:
            if modules.exists() and stamp.exists() and stamp.read_text(encoding="ascii").strip() == lock_digest:
                return True
        except OSError:
            pass
        try:
            LOGGER.info("Installing locked WhatsApp worker dependencies")
            subprocess.run(
                [npm, "ci", "--omit=dev", "--no-audit", "--no-fund"],
                cwd=self.worker_dir,
                check=True,
                timeout=180,
                stdout=subprocess.DEVNULL,
            )
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(lock_digest + "\n", encoding="ascii")
            return True
        except Exception as exc:
            LOGGER.error("WhatsApp worker bootstrap failed: %s", exc)
            self._set_state("UNAVAILABLE", "WhatsApp-Komponente konnte nicht installiert werden")
            return False

    def start(self) -> None:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return
            if not self.bootstrap():
                return
            self.auth_dir.mkdir(parents=True, exist_ok=True)
            self._stopping = False
            self._set_state("CONNECTING")
            env = os.environ.copy()
            env["LADIES_WHATSAPP_AUTH_DIR"] = str(self.auth_dir)
            proc = subprocess.Popen(
                [_runtime_executable("node") or "node", "worker.mjs"],
                cwd=self.worker_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
            self.proc = proc
            threading.Thread(target=self._read_stdout, args=(proc,), daemon=True, name="wa-json").start()
            threading.Thread(target=self._read_stderr, args=(proc,), daemon=True, name="wa-log").start()
            threading.Thread(target=self._watch, args=(proc,), daemon=True, name="wa-watch").start()

    def _read_stdout(self, proc: subprocess.Popen[str]) -> None:
        if not proc.stdout:
            return
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except Exception:
                LOGGER.warning("Ignoring invalid worker JSON")
                continue
            try:
                self._handle(event)
            except Exception:
                LOGGER.exception("WhatsApp worker event handling failed")

    @staticmethod
    def _safe_worker_log(line: str) -> str:
        return re.sub(r"(?<!\d)(\d{3})\d{4,}(\d{4})(?!\d)", r"\1******\2", line)[:1200]

    def _read_stderr(self, proc: subprocess.Popen[str]) -> None:
        if not proc.stderr:
            return
        for line in proc.stderr:
            LOGGER.info("baileys: %s", self._safe_worker_log(line.rstrip()))

    def _watch(self, proc: subprocess.Popen[str]) -> None:
        code = proc.wait()
        with self._lock:
            if self.proc is proc:
                self.proc = None
            if self._stopping or self.state == "LOGGED_OUT":
                return
            delay = RESTART_DELAYS[min(self._restart_attempt, len(RESTART_DELAYS) - 1)]
            self._restart_attempt += 1
            self._set_state("ERROR", f"WhatsApp-Worker beendet (Code {code})")
        LOGGER.warning("WhatsApp worker crashed code=%s restart_in=%ss", code, delay)
        time.sleep(delay)
        with self._lock:
            if self._stopping or self.state == "LOGGED_OUT":
                return
        self._set_state("RECONNECTING", f"Worker-Neustart nach {delay}s")
        self.start()

    def _handle(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "qr":
            self.qr = event.get("value")
            self._set_state("QR_REQUIRED")
        elif event_type == "connection":
            state = str(event.get("state") or "").upper()
            if state in ALLOWED_STATES:
                self.state = state
                self.detail = event.get("detail")
            if state == "CONNECTED":
                self._restart_attempt = 0
                self.qr = None
                self.account = event.get("account")
                self.last_connected_at = time.time()
        request_id = event.get("id")
        target = None
        if request_id:
            with self._lock:
                target = self._pending.get(request_id)
        if target is not None:
            try:
                target.put_nowait(event)
            except queue.Full:
                pass
        self.event_handler(event)

    def command(self, command_type: str, timeout: int = 15, **payload):
        self.start()
        with self._lock:
            proc = self.proc
        if not proc or proc.poll() is not None:
            raise RuntimeError(self.detail or "WhatsApp-Worker nicht verfügbar")
        request_id = uuid.uuid4().hex
        response_queue: queue.Queue = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[request_id] = response_queue
        try:
            message = {"id": request_id, "type": command_type, **payload}
            if not proc.stdin:
                raise RuntimeError("WhatsApp-Worker hat keinen Eingabekanal")
            with self._write_lock:
                proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            response = response_queue.get(timeout=timeout)
            if response.get("ok") is False:
                raise RuntimeError(str(response.get("error") or "WhatsApp-Befehl fehlgeschlagen"))
            return response
        except queue.Empty as exc:
            raise RuntimeError("WhatsApp-Worker antwortet nicht") from exc
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            proc = self.proc
            self.proc = None
        if not proc:
            return
        try:
            if proc.stdin and proc.poll() is None:
                with self._write_lock:
                    proc.stdin.write(json.dumps({"id": uuid.uuid4().hex, "type": "shutdown"}) + "\n")
                    proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
