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
APP_STATE_RECOVERY_WINDOW_SECONDS = 15.0
APP_STATE_RECOVERY_COOLDOWN_SECONDS = 300.0
APP_STATE_MISSING_KEY_RE = re.compile(r"failed to find key .* to decode mutation", re.I)


class WhatsAppGateway:
    """Process/IPC boundary for the Baileys worker.

    Runtime provisioning belongs to ``ladies_explorer.py``. This class only
    verifies that the prepared runtime exists and then manages one worker
    process plus its request/response IPC contract. A worker process may only
    exist while an explicit connection intent is active.
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
        self._stopping = True
        self._desired_running = False
        self._generation = 0
        self._restart_attempt = 0
        self._app_state_failures: list[float] = []
        self._last_app_state_recovery = 0.0
        self._app_state_recovery_running = False
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

    def activate(self) -> None:
        """Enable the worker for an explicit connect/resume intent."""
        with self._lock:
            if not self._desired_running:
                self._desired_running = True
                self._generation += 1
            self._stopping = False
        self.start()

    def start(self) -> None:
        with self._lock:
            # Legacy callers may still ask the gateway to start during service
            # construction. Without an explicit connection intent this is a
            # deliberate no-op; the connection lifecycle is the sole owner.
            if not self._desired_running:
                return
            if self.proc and self.proc.poll() is None:
                return
            ok, detail = self.dependency_status()
            if not ok:
                self._set_state("DEPENDENCY_MISSING", detail)
                return
            self.auth_dir.mkdir(parents=True, exist_ok=True)
            self._stopping = False
            generation = self._generation
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
            threading.Thread(target=self._watch, args=(proc, generation), daemon=True, name="wa-watch").start()

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

    @staticmethod
    def _is_app_state_missing_key_log(line: str) -> bool:
        try:
            payload = json.loads(line)
            text = " ".join(str(payload.get(key) or "") for key in ("error", "msg", "reason"))
        except (TypeError, ValueError, json.JSONDecodeError):
            text = line
        return bool(APP_STATE_MISSING_KEY_RE.search(text))

    def _clear_app_state_versions(self) -> int:
        """Drop only app-state cursors; credentials and sync keys stay intact."""
        removed = 0
        for path in self.auth_dir.glob("app-state-sync-version-*.json"):
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                continue
            except OSError:
                LOGGER.warning("Could not remove stale WhatsApp app-state cursor path=%s", path.name)
        return removed

    def _maybe_schedule_app_state_recovery(self, line: str, proc: subprocess.Popen[str]) -> None:
        if not self._is_app_state_missing_key_log(line):
            return
        now = time.monotonic()
        with self._lock:
            cutoff = now - APP_STATE_RECOVERY_WINDOW_SECONDS
            self._app_state_failures = [seen for seen in self._app_state_failures if seen >= cutoff]
            self._app_state_failures.append(now)
            if len(self._app_state_failures) < 2:
                return
            if self._app_state_recovery_running:
                return
            if now - self._last_app_state_recovery < APP_STATE_RECOVERY_COOLDOWN_SECONDS:
                return
            if proc is not self.proc or proc.poll() is not None or self._stopping:
                return
            self._app_state_recovery_running = True
            self._last_app_state_recovery = now
        threading.Thread(
            target=self._recover_app_state,
            args=(proc,),
            daemon=True,
            name="wa-app-state-recovery",
        ).start()

    def _recover_app_state(self, proc: subprocess.Popen[str]) -> None:
        try:
            with self._lock:
                if proc is not self.proc or proc.poll() is not None or self._stopping:
                    return
            removed = self._clear_app_state_versions()
            LOGGER.warning(
                "WhatsApp app-state decode repeatedly missed a sync key; "
                "resetting %s version cursor(s) and restarting worker while preserving credentials/keys",
                removed,
            )
            with self._lock:
                if proc is not self.proc or proc.poll() is not None or self._stopping:
                    return
                self._set_state("RECONNECTING", "WhatsApp-App-State wird neu synchronisiert")
            proc.terminate()
        except Exception:
            LOGGER.exception("WhatsApp app-state recovery failed")
        finally:
            with self._lock:
                self._app_state_recovery_running = False
                self._app_state_failures.clear()

    def _read_stderr(self, proc: subprocess.Popen[str]) -> None:
        if not proc.stderr:
            return
        for line in proc.stderr:
            raw = line.rstrip()
            self._maybe_schedule_app_state_recovery(raw, proc)
            level, safe = self._worker_log_level(raw)
            LOGGER.log(level, "baileys: %s", safe)

    def _watch(self, proc: subprocess.Popen[str], generation: int) -> None:
        code = proc.wait()
        with self._lock:
            if self.proc is proc:
                self.proc = None
            if (
                self._stopping
                or not self._desired_running
                or generation != self._generation
                or self.state == "LOGGED_OUT"
            ):
                return
            delay = RESTART_DELAYS[min(self._restart_attempt, len(RESTART_DELAYS) - 1)]
            self._restart_attempt += 1
            self._set_state("ERROR", f"WhatsApp-Worker beendet (Code {code})")
        LOGGER.warning("WhatsApp worker crashed code=%s restart_in=%ss", code, delay)
        time.sleep(delay)
        with self._lock:
            if (
                self._stopping
                or not self._desired_running
                or generation != self._generation
                or self.state == "LOGGED_OUT"
            ):
                return
        try:
            self._set_state("RECONNECTING", f"Worker-Neustart nach {delay}s")
            self.start()
            # The worker is intentionally idle on process startup. Reconnect
            # explicitly so a crashed registered session really resumes.
            self.command("connect", timeout=15)
        except Exception:
            with self._lock:
                if self._desired_running and generation == self._generation and not self._stopping:
                    self._set_state("ERROR", "WhatsApp-Worker konnte nicht neu verbunden werden")
            LOGGER.warning("WhatsApp worker restart failed", exc_info=True)

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
            if command_type == "connect":
                self.activate()
            else:
                self.start()
            with self._lock:
                proc = self.proc
            if not proc or proc.poll() is not None:
                LOGGER.warning(
                    "WhatsApp command unavailable type=%s detail=%s",
                    command_type,
                    self._safe_worker_log(str(self.detail or "-")),
                )
                raise RuntimeError("WhatsApp ist derzeit nicht verfügbar")
            request_id = uuid.uuid4().hex
            response_queue: queue.Queue = queue.Queue(maxsize=1)
            with self._lock:
                self._pending[request_id] = response_queue
            try:
                raw = json.dumps({"id": request_id, "type": command_type, **payload}, ensure_ascii=False)
                if not proc.stdin:
                    LOGGER.warning("WhatsApp worker stdin unavailable type=%s", command_type)
                    raise RuntimeError("WhatsApp ist derzeit nicht verfügbar")
                with self._write_lock:
                    proc.stdin.write(raw + "\n")
                    proc.stdin.flush()
                try:
                    event = response_queue.get(timeout=timeout)
                except queue.Empty:
                    LOGGER.warning("WhatsApp worker command timed out type=%s timeout=%s", command_type, timeout)
                    raise RuntimeError("WhatsApp hat nicht rechtzeitig geantwortet") from None
                if event.get("ok") is False:
                    LOGGER.warning(
                        "WhatsApp worker command rejected type=%s detail=%s",
                        command_type,
                        self._safe_worker_log(str(event.get("message") or "Worker-Fehler")),
                    )
                    raise RuntimeError("WhatsApp-Aktion konnte nicht ausgeführt werden")
                return event
            finally:
                with self._lock:
                    self._pending.pop(request_id, None)

    def stop(self) -> None:
        """Stop the worker and invalidate every pending automatic restart."""
        with self._lock:
            self._desired_running = False
            self._stopping = True
            self._generation += 1
            self._restart_attempt = 0
            proc = self.proc
            if not proc:
                return
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
