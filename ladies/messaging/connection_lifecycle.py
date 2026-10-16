from __future__ import annotations

import base64
import io
import json
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import qrcode

LOGGER = logging.getLogger("ladies_explorer.messaging.connection")
AUTO_RESUME_DELAYS = (0.0, 1.0, 2.0)


class WhatsAppConnectionLifecycle:
    """Own connection intent and persisted Baileys auth independently from chats."""

    def __init__(self, service) -> None:
        self.service = service
        self._lock = threading.RLock()
        self._intent_generation = 0
        self._pairing_requested = False
        self._qr_source: str | None = None
        self._qr_rendered: str | None = None

        if self.service.gateway.state == "DEPENDENCY_MISSING":
            return

        self.service.gateway.stop()
        self.service.gateway.state = "NOT_CONFIGURED"
        self.service.gateway.detail = None

        if self.has_saved_session():
            generation = self._intent_generation
            gateway = self.service.gateway
            try:
                activate = getattr(gateway, "activate", None)
                if callable(activate):
                    activate()
            except Exception:
                gateway.stop()
                gateway.state = "ERROR"
                gateway.detail = "Gespeicherte WhatsApp-Verbindung konnte nicht gestartet werden."
                LOGGER.warning("Could not activate registered WhatsApp session", exc_info=True)
                return
            threading.Thread(
                target=self._resume_saved_session,
                args=(generation,),
                daemon=True,
                name="wa-resume-session",
            ).start()

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

    def has_local_auth_data(self) -> bool:
        try:
            return any(path.is_file() for path in self.auth_dir.rglob("*"))
        except (FileNotFoundError, OSError):
            return False

    def _resume_saved_session(self, generation: int) -> None:
        last_error: Exception | None = None
        for delay in AUTO_RESUME_DELAYS:
            if delay:
                time.sleep(delay)
            with self._lock:
                if generation != self._intent_generation or not self.has_saved_session():
                    return
                gateway = self.service.gateway

            ok, detail = gateway.dependency_status()
            if not ok:
                with self._lock:
                    if generation != self._intent_generation or not self.has_saved_session():
                        return
                    gateway.stop()
                    gateway.state = "DEPENDENDENCY_MISSING"
                    gateway.detail = detail
                return

            try:
                gateway.command("connect", timeout=15)
                return
            except Exception as exc:
                last_error = exc
                LOGGER.info("Registered WhatsApp session resume attempt failed", exc_info=True)

        with self._lock:
            if generation != self._intent_generation or not self.has_saved_session():
                return
            gateway = self.service.gateway
            gateway.stop()
            gateway.state = "ERROR"
            gateway.detail = "Gespeicherte WhatsApp-Verbindung konnte nicht wiederhergestellt werden."
        LOGGER.warning("Could not resume registered WhatsApp session: %s", last_error)

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
                has_local_auth_data=bool(self.has_local_auth_data()),
                qr_available=bool(visible_qr),
                qr=visible_qr,
            )
            return payload

    def connect(self) -> dict[str, Any]:
        with self._lock:
            self._intent_generation += 1
            gateway = self.service.gateway
            if gateway.state == "CONNECTED":
                self._pairing_requested = False
                return self.status()

            self._pairing_requested = not self.has_saved_session()
            if self._pairing_requested:
                self._clear_qr()
            ok, detail = gateway.dependency_status()
            if not ok:
                gateway.stop()
                gateway.state = "DEPENDENCY_MISSING"
                gateway.detail = detail
                self._pairing_requested = False
                return self.status()
            try:
                gateway.command("connect", timeout=15)
            except Exception:
                LOGGER.warning("WhatsApp connect command failed", exc_info=True)
                gateway.stop()
                gateway.state = "ERROR"
                gateway.detail = "WhatsApp-Verbindung konnte nicht gestartet werden."
                self._pairing_requested = False
            return self.status()

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            self._intent_generation += 1
            self._pairing_requested = False
            self._clear_qr()
            gateway = self.service.gateway
            had_saved_session = self.has_saved_session()
            try:
                self.service.disconnect()
            finally:
                gateway.stop()
            gateway.detail = None
            gateway.state = "DISCONNECTED" if had_saved_session else "NOT_CONFIGURED"
            return self.status()

    def _delete_auth_state(self) -> None:
        """Delete all local Baileys auth files and verify that no data remains."""
        last_error: OSError | None = None
        for attempt in range(3):
            try:
                shutil.rmtree(self.auth_dir)
                last_error = None
            except FileNotFoundError:
                return
            except OSError as exc:
                last_error = exc

            if not self.has_local_auth_data():
                try:
                    self.auth_dir.rmdir()
                except (FileNotFoundError, OSError):
                    pass
                return

            if attempt < 2:
                time.sleep(0.1 * (attempt + 1))

        if self.has_local_auth_data():
            LOGGER.error("WhatsApp auth reset left local files behind: %s", last_error)
            raise RuntimeError("WhatsApp-Verbindungsdaten konnten nicht vollständig gelöscht werden") from last_error

    def reset(self) -> dict[str, Any]:
        """Stop connection intent and delete the complete local Baileys auth state.

        Messages, media and chat history live outside the Baileys auth state and
        are preserved. Session-bound pending ingress is quarantined before auth
        deletion so it can never be replayed into a newly paired account.
        """
        with self._lock:
            self._intent_generation += 1
            self._pairing_requested = False
            self._clear_qr()
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
            gateway.stop()
            abandon = getattr(gateway, "discard_pending_ingress", None)
            if callable(abandon):
                moved = abandon(reason="reset")
                if moved:
                    LOGGER.warning("Quarantined %s pending WhatsApp ingress event(s) during reset", moved)

            try:
                self._delete_auth_state()
            except RuntimeError as exc:
                gateway.account = None
                gateway.last_connected_at = None
                gateway.state = "ERROR"
                gateway.detail = str(exc)
                try:
                    self.service.events.publish({"type": "connection", "state": "ERROR", "detail": str(exc)})
                except Exception:
                    LOGGER.debug("Could not publish WhatsApp reset failure", exc_info=True)
                raise

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
