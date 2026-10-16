from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


MAX_DEVICE_SESSIONS = 32
SESSION_TOUCH_INTERVAL = timedelta(minutes=5)


class LanAccessManager:
    """Pair LAN devices with process-scoped pairing tokens and persistent device sessions."""

    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "lan_access.json"
        self.legacy_path = self.root / "lan_access_token"
        self._lock = threading.RLock()
        self._state = self._load_or_create()

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _legacy_token(self) -> str | None:
        try:
            value = self.legacy_path.read_text(encoding="utf-8").strip()
            return value if len(value) >= 24 else None
        except OSError:
            return None

    def _load_or_create(self) -> dict[str, Any]:
        legacy = self._legacy_token()
        state: dict[str, Any] | None = None
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("master_secret"):
                state = loaded
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            state = None

        if state is None:
            state = {
                "version": 1,
                "master_secret": secrets.token_urlsafe(48),
                "legacy_session_hash": self._hash(legacy) if legacy else None,
                "sessions": {},
            }
        else:
            state.setdefault("version", 1)
            state.setdefault("sessions", {})
            if not state.get("legacy_session_hash") and legacy:
                state["legacy_session_hash"] = self._hash(legacy)
            else:
                state.setdefault("legacy_session_hash", None)

        # Pairing tokens are process-scoped, not browser sessions.
        state["pairing_token"] = secrets.token_urlsafe(32)
        self._trim_sessions(state)
        self._write(state)

        # Remove reusable plaintext credentials after storing their one-way hash.
        if legacy:
            try:
                self.legacy_path.unlink()
            except OSError:
                pass
        return state

    @staticmethod
    def _trim_sessions(state: dict[str, Any]) -> None:
        sessions = state.get("sessions")
        if not isinstance(sessions, dict):
            state["sessions"] = {}
            return
        if len(sessions) <= MAX_DEVICE_SESSIONS:
            return
        ordered = sorted(
            sessions.items(),
            key=lambda item: str(
                (item[1] or {}).get("last_seen_at")
                or (item[1] or {}).get("created_at")
                or ""
            ),
            reverse=True,
        )
        state["sessions"] = dict(ordered[:MAX_DEVICE_SESSIONS])

    def _write(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_name(f".{self.state_path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.state_path)
        try:
            os.chmod(self.state_path, 0o600)
        except OSError:
            pass

    @property
    def pairing_token(self) -> str:
        with self._lock:
            return str(self._state["pairing_token"])

    def pair(self, token: str) -> str | None:
        with self._lock:
            if not token or not hmac.compare_digest(token, str(self._state.get("pairing_token") or "")):
                return None
            session = secrets.token_urlsafe(48)
            digest = self._hash(session)
            now = self._now()
            self._state.setdefault("sessions", {})[digest] = {
                "created_at": now,
                "last_seen_at": now,
            }
            self._trim_sessions(self._state)
            self._write(self._state)
            return session

    def valid_session(self, token: str | None) -> bool:
        if not token:
            return False
        digest = self._hash(token)
        with self._lock:
            sessions = self._state.get("sessions")
            if isinstance(sessions, dict) and digest in sessions:
                row = sessions[digest]
                now = datetime.now(timezone.utc)
                previous = None
                try:
                    previous_raw = str(row.get("last_seen_at") or "")
                    if previous_raw:
                        previous = datetime.fromisoformat(previous_raw)
                except (TypeError, ValueError):
                    previous = None
                if previous is None or now - previous >= SESSION_TOUCH_INTERVAL:
                    row["last_seen_at"] = now.isoformat()
                    self._write(self._state)
                return True
            legacy_hash = self._state.get("legacy_session_hash")
            return bool(legacy_hash and hmac.compare_digest(digest, str(legacy_hash)))

    def revoke_all(self) -> None:
        with self._lock:
            self._state["sessions"] = {}
            self._state["legacy_session_hash"] = None
            self._write(self._state)
