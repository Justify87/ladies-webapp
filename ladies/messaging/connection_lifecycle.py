from __future__ import annotations

import base64
import io
import json
import logging
import shutil
import threading
from pathlib import Path
from typing import Any

import qrcode

LOGGER = logging.getLogger("ladies_explorer.messaging.connection")


class WhatsAppConnectionLifecycle:
    """Own pairing intent and persisted Baileys auth independently from chats."""

    def __init__(self, service) -> None:
        self.service = service
        self._lock = threading.RLock()
        self._pairing_requested = False
        self._qr_source: str | None = None
        self._qr_rendered: str | None = None

        # MessagingService historically starts the worker whenever creds.json
        # merely exists. Baileys creates that file before a device is paired,
        # so an interrupted first pairing must not become an implicit startup
        # connection on the next app launch. Dependency failures remain visible.
        if self.service.gateway.state == "DEPENDENCY_MISSING":
            return
        if self.has_saved_session():
            threading.Thread(
                target=self._resume_saved_session,
                daemon=True,
                name="wa-resume-session",
            ).start()
        else:
            self.service.gateway.stop()
            self.service.gateway.state = "NOT_CONFIGURED"
            self.service.gateway.detail = None

    @property
    def auth_dir(self) -> Path:
        return Path(self.service.auth_dir)

    def has_saved_session(self) -> bool:
        if self.service.gateway.state == "CONNECTED":
            return True
        credentials = self.auth_dir / "creds.json"
        try:
            payload = json.loads(credentials.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return False
        return payload.get("registered") is True

    def _resume_saved_session(self) -> None:
        try:
            self.service.gateway.command("connect", timeout=15)
        except Exception:
            LOGGER.warning("Could not resume registered WhatsApp session", exc_info=True)

    def _clear_qr(self) -> None:
        self._qr_source = None
        self._qr_rendered = None
        self.service.gateway.qr = None

    def _qr_data_uri(self, value: str | None) -> str | None:
        if not value:
            return None
        if value == self._qr_source and self._qr_rendered:
            return self._qr_rendered
        image = qrcode.make(value)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        self._qr_source = value
        self._qr_rendered = f"data:image/png;base64,{encoded}"
        return self._qr_rendered

    def status(self) -> dict[str, Any]:
        with self._lock:
            payload = dict(self.service.status())
            raw_state = str(payload.get("state") or "NOT_CONFIGURED").upper()
            if raw_state == "CONNECTED":
                self._pairing_requested = False
                self._qr_source = None
                self._qr_rendered = None

            has_saved_session = self.has_saved_session()
            state = raw_state
            detail = payload.get("detail")

            # QR material is never surfaced unless this process recorded an
            # explicit user request to establish a new pairing.
            if raw_state == "QR_REQUIRED" and not self._pairing_requested:
                state = "AUTH_REQUIRED"
                detail = "Die gespeicherte WhatsApp-Anmeldung muss neu eingerichtet werden."
            elif raw_state == "LOGGED_OUT":
                self._pairing_requested = False
                state = "AUTH_REQUIRED" if has_saved_session else "NOT_CONFIGURED"
                detail = "Die WhatsApp-Anmeldung ist nicht mehr gültig." if has_saved_session else None
            elif raw_state == "DISCONNECTED" and not has_saved_session:
                state = "NOT_CONFIGURED"
            elif raw_state == "ERROR" and str(detail or "").startswith("pairing_failed_code_"):
                code = str(detail).removeprefix("pairing_failed_code_")
                self._pairing_requested = False
                self._clear_qr()
                detail = (
                    f"WhatsApp hat den Kopplungsaufbau abgelehnt (Code {code}). Bitte erneut verbinden."
                    if code and code != "unknown"
                    else "WhatsApp hat den Kopplungsaufbau abgelehnt. Bitte erneut verbinden."
                )
                self.service.gateway.detail = detail

            raw_qr = self.service.qr() if self._pairing_requested and state == "QR_REQUIRED" else None
            visible_qr = self._qr_data_uri(raw_qr)
            payload.update(
                state=state,
                detail=detail,
                pairing_requested=bool(self._pairing_requested),
                has_saved_session=bool(has_saved_session),
                qr_available=bool(visible_qr),
                qr=visible_qr,
            )
            return payload

    def connect(self) -> dict[str, Any]:
        with self._lock:
            gateway = self.service.gateway
            if gateway.state == "CONNECTED":
                self._pairing_requested = False
                return self.status()
            # Existing registered credentials mean reconnect, not pairing.
            self._pairing_requested = not self.has_saved_session()
            if self._pairing_requested:
                self._clear_qr()
            ok, detail = gateway.dependency_status()
            if not ok:
                gateway.state = "DEPENDENCY_MISSING"
                gateway.detail = detail
                self._pairing_requested = False
                return self.status()
            try:
                # The worker itself stays idle after process start. Only this
                # explicit lifecycle operation is allowed to begin pairing.
                gateway.command("connect", timeout=15)
            except Exception:
                LOGGER.warning("WhatsApp connect command failed", exc_info=True)
                gateway.state = "ERROR"
                gateway.detail = "WhatsApp-Verbindung konnte nicht gestartet werden."
                self._pairing_requested = False
            return self.status()

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            self._pairing_requested = False
            self._clear_qr()
            self.service.disconnect()
            if not self.has_saved_session() and self.service.gateway.state == "DISCONNECTED":
                self.service.gateway.state = "NOT_CONFIGURED"
                self.service.gateway.detail = None
            return self.status()

    def reset(self) -> dict[str, Any]:
        """Revoke the live link when possible and delete the complete auth state.

        Baileys credentials and Signal keys are one session. Reset therefore
        removes the whole auth directory while leaving messages/media/history
        untouched in their separate stores.
        """
        with self._lock:
            self._pairing_requested = False
            gateway = self.service.gateway
            proc = gateway.proc
            if proc is not None and proc.poll() is None:
                try:
                    gateway.command("logout", timeout=10)
                except Exception:
                    LOGGER.warning(
                        "WhatsApp remote logout failed; local auth state will still be reset",
                        exc_info=True,
                    )
                finally:
                    gateway.stop()
            else:
                gateway.stop()

            shutil.rmtree(self.auth_dir, ignore_errors=True)
            self.auth_dir.mkdir(parents=True, exist_ok=True)
            self._clear_qr()
            gateway.account = None
            gateway.last_connected_at = None
            gateway.detail = None
            gateway.state = "NOT_CONFIGURED"
            try:
                self.service.events.publish({"type": "connection", "state": "NOT_CONFIGURED", "detail": None})
            except Exception:
                LOGGER.debug("Could not publish WhatsApp reset event", exc_info=True)
            return self.status()
