from __future__ import annotations

import atexit
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
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable

LOGGER = logging.getLogger("ladies_explorer.messaging.gateway")
ALLOWED_STATES = {
    "UNAVAILABLE", "DEPENDENCY_MISSING", "NOT_CONFIGURED", "QR_REQUIRED",
    "PAIRING", "CONNECTING", "CONNECTED", "DISCONNECTED", "RECONNECTING",
    "LOGGED_OUT", "ERROR",
}
RESTART_DELAYS = (1, 2, 5, 10, 30)
SERIAL_COMMANDS = {
    "connect", "disconnect", "logout", "send_text", "send_media",
    "send_reaction", "edit_message", "delete_message",
}


class WhatsAppGateway:
    """Process/IPC boundary for the Baileys worker.

    Runtime provisioning belongs to ``ladies_explorer.py``. This class only
    verifies that the prepared runtime exists and then manages one worker
    process plus its request/response IPC contract.
    """

    def __init__(self, worker_dir: Path, auth_dir: Path, event_handler: Callable[[dict[str, Any]], None]):
        self.worker_dir = Path(worker_dir)
        self.auth_dir = Path(auth_dir)
        self.event_handler = event_handler
        self.proc: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._serial_lock = threading.Lock()
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
        previous = self.state
        self.state = state
        self.detail = detail
        if state in {"ERROR", "LOGGED_OUT"}:
            LOGGER.warning("WhatsApp state %s -> %s detail=%s", previous, state, detail or "-")
        elif state == "CONNECTED" and previous != state:
            LOGGER.info("WhatsApp connected")
        self.event_handler({"type": "connection", "state": state, "detail": detail})

    def dependency_status(self) -> tuple[bool, str | None]:
        node = shutil.which("node")
        if not node:
            return False, "Node.js 20+ fehlt; starte Ladies Explorer über den Launcher"
        try:
            major = int(subprocess.check_output(
                [node, "-p", "process.versions.node.split('.')[0]"],
                text=True,
                timeout=5,
            ).strip())
            if major < 20:
                return False, "Node.js 20+ erforderlich"
        except Exception:
            return False, "Node.js-Version konnte nicht geprüft werden"
        if not (self.worker_dir / "worker.mjs").is_file():
            return False, "WhatsApp-Worker fehlt"
        if not (self.worker_dir / "node_modules" / "@whiskeysockets" / "baileys").exists():
            return False, "WhatsApp-Abhängigkeiten fehlen; starte Ladies Explorer über den Launcher"
        return True, None

    def start(self) -> None:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return
            ok, detail = self.dependency_status()
            if not ok:
                self._set_state("DEPENDENCY_MISSING", detail)
                return
            self.auth_dir.mkdir(parents=True, exist_ok=True)
            self._stopping = False
            self._set_state("CONNECTING")
            env = os.environ.copy()
            env["LADIES_WHATSAPP_AUTH_DIR"] = str(self.auth_dir)
            proc = subprocess.Popen(
                [shutil.which("node") or "node", "worker.mjs"],
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
        safe = re.sub(r"(?<!\d)(\d{3})\d{4,}(\d{4})(?!\d)", r"\1******\2", line)
        safe = re.sub(r"\b[0-9A-Za-z_.:-]{5,}@(s\.whatsapp\.net|lid|g\.us)\b", "[WHATSAPP_ADDRESS]", safe, flags=re.I)
        return safe[:1200]

    @staticmethod
    def _worker_log_level(line: str) -> tuple[int, str]:
        safe = WhatsAppGateway._safe_worker_log(line)
        try:
            payload = json.loads(line)
            level = int(payload.get("level") or 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            return logging.WARNING, safe
        if level >= 60:
            return logging.CRITICAL, safe
        if level >= 50:
            return logging.ERROR, safe
        if level >= 40:
            return logging.WARNING, safe
        return logging.DEBUG, safe

    def _read_stderr(self, proc: subprocess.Popen[str]) -> None:
        if not proc.stderr:
            return
        for line in proc.stderr:
            level, safe = self._worker_log_level(line.rstrip())
            LOGGER.log(level, "baileys: %s", safe)

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
                previous = self.state
                self.state = state
                self.detail = event.get("detail")
                if state in {"ERROR", "LOGGED_OUT"}:
                    LOGGER.warning("WhatsApp state %s -> %s detail=%s", previous, state, self.detail or "-")
                elif state == "CONNECTED" and previous != state:
                    LOGGER.info("WhatsApp connected")
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
        guard = self._serial_lock if command_type in SERIAL_COMMANDS else nullcontext()
        with guard:
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
                raw = json.dumps({"id": request_id, "type": command_type, **payload}, ensure_ascii=False)
                if not proc.stdin:
                    raise RuntimeError("Worker stdin nicht verfügbar")
                with self._write_lock:
                    proc.stdin.write(raw + "\n")
                    proc.stdin.flush()
                event = response_queue.get(timeout=timeout)
                if event.get("ok") is False:
                    raise RuntimeError(event.get("message") or "Worker-Fehler")
                return event
            finally:
                with self._lock:
                    self._pending.pop(request_id, None)

    def stop(self) -> None:
        with self._lock:
            proc = self.proc
            if not proc:
                return
            self._stopping = True
        try:
            request_id = uuid.uuid4().hex
            if proc.stdin:
                with self._write_lock:
                    proc.stdin.write(json.dumps({"id": request_id, "type": "shutdown"}) + "\n")
                    proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        with self._lock:
            if self.proc is proc:
                self.proc = None
