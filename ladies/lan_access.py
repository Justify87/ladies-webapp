from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_DEVICE_SESSIONS = 32


class LanAccessManager:
    """Pair LAN devices without reusing the pairing secret as session cookie.

    Existing V9.5 ``lan_access_token`` cookies remain valid as a migration-only
    legacy session. The raw legacy token is replaced by its hash and the old file
    is removed. New pairings receive independent random device sessions. The
    pairing token rotates on every server start while device sessions survive.
    """

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

        # Pairing credentials are per application start and are not browser
        # sessions. A new process always gets a fresh pairing token.
        state["pairing_token"] = secrets.token_urlsafe(32)
        self._trim_sessions(state)
        self._write(state)

        # Once the legacy value has been persisted as a one-way hash, keeping the
        # original reusable token on disk only increases credential exposure.
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
            key=lambda item: str((item[1] or {}).get("created_at") or ""),
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
            if not hmac.compare_digest(str(token), str(self._state["pairing_token"])):
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

    def validate_session(self, session: str | None) -> bool:
        if not session:
            return False
        digest = self._hash(str(session))
        with self._lock:
            legacy = self._state.get("legacy_session_hash")
            if legacy and hmac.compare_digest(digest, str(legacy)):
                return True
            sessions = self._state.get("sessions") or {}
            row = sessions.get(digest)
            if not isinstance(row, dict):
                return False
            row["last_seen_at"] = self._now()
            return True
