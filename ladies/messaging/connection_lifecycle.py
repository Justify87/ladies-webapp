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
from flask import jsonify

LOGGER = logging.getLogger("ladies_explorer.messaging.connection")


class WhatsAppConnectionLifecycle:
    """Own the WhatsApp connection/session lifecycle independently from chats.

    The messaging service remains responsible for conversations and worker
    events. This coordinator owns user pairing intent, visibility of QR data,
    and destructive auth-state reset so disconnecting can never accidentally
    delete a reusable Baileys session.
    """

    def __init__(self, service) -> None:
        self.service = service
        self._lock = threading.RLock()
        self._pairing_requested = False

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

    @staticmethod
    def _qr_data_uri(value: str | None) -> str | None:
        if not value:
            return None
        image = qrcode.make(value)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def status(self) -> dict[str, Any]:
        with self._lock:
            payload = dict(self.service.status())
            raw_state = str(payload.get("state") or "NOT_CONFIGURED").upper()
            if raw_state == "CONNECTED":
                self._pairing_requested = False

            has_saved_session = self.has_saved_session()
            state = raw_state
            detail = payload.get("detail")

            # A QR emitted without an explicit user pairing request must never
            # become visible. This commonly means persisted auth is unusable.
            if raw_state == "QR_REQUIRED" and not self._pairing_requested:
                state = "AUTH_REQUIRED"
                detail = "Die gespeicherte WhatsApp-Anmeldung muss neu eingerichtet werden."
            elif raw_state == "LOGGED_OUT":
                self._pairing_requested = False
                state = "AUTH_REQUIRED" if has_saved_session else "NOT_CONFIGURED"
                detail = "Die WhatsApp-Anmeldung ist nicht mehr gültig." if has_saved_session else None
            elif raw_state == "DISCONNECTED" and not has_saved_session:
                state = "NOT_CONFIGURED"

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
            if self.service.gateway.state == "CONNECTED":
                self._pairing_requested = False
                return self.status()
            # Pairing is an explicit user action only when no reusable session
            # exists. Reconnects with saved credentials never expose a QR.
            self._pairing_requested = not self.has_saved_session()
            if self._pairing_requested:
                self.service.gateway.qr = None
            self.service.connect()
            return self.status()

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            self._pairing_requested = False
            self.service.gateway.qr = None
            self.service.disconnect()
            if not self.has_saved_session() and self.service.gateway.state == "DISCONNECTED":
                self.service.gateway.state = "NOT_CONFIGURED"
                self.service.gateway.detail = None
            return self.status()

    def reset(self) -> dict[str, Any]:
        """Revoke the live link when possible, then remove all local auth state.

        Baileys credentials and Signal keys form one session. Reset therefore
        deletes the complete auth directory, never only creds.json.
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

            gateway.qr = None
            gateway.account = None
            gateway.last_connected_at = None
            gateway.detail = None
            gateway.state = "NOT_CONFIGURED"
            try:
                self.service.events.publish({"type": "connection", "state": "NOT_CONFIGURED", "detail": None})
            except Exception:
                LOGGER.debug("Could not publish WhatsApp reset event", exc_info=True)
            return self.status()


def install_connection_lifecycle(app, service) -> WhatsAppConnectionLifecycle:
    """Replace legacy connection endpoints at the messaging composition edge."""
    lifecycle = WhatsAppConnectionLifecycle(service)
    app.extensions["ladies_whatsapp_connection"] = lifecycle

    def messaging_status():
        return jsonify(lifecycle.status())

    def messaging_connect():
        return jsonify(lifecycle.connect())

    def messaging_disconnect():
        return jsonify(lifecycle.disconnect())

    def messaging_logout():
        # Kept as a narrow compatibility alias while the UI migrates from the
        # old "logout" wording. Semantics are now a full auth-state reset.
        return jsonify(lifecycle.reset())

    def messaging_qr():
        return jsonify(error="Der QR-Code wird direkt in den Einstellungen angezeigt."), 410

    replacements = {
        "messaging_status": messaging_status,
        "messaging_connect": messaging_connect,
        "messaging_disconnect": messaging_disconnect,
        "messaging_logout": messaging_logout,
        "messaging_qr": messaging_qr,
    }
    missing = [name for name in replacements if name not in app.view_functions]
    if missing:
        raise RuntimeError(f"Messaging connection routes missing: {', '.join(missing)}")
    app.view_functions.update(replacements)

    @app.delete("/api/messaging/connection")
    def messaging_reset_connection():
        return jsonify(lifecycle.reset())

    return lifecycle
