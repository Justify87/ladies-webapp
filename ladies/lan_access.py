from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from pathlib import Path

PAIRING_FILE = "lan_pairing_token"
LEGACY_ACCESS_FILE = "lan_access_token"
SESSIONS_FILE = "lan_device_sessions.json"
MAX_DEVICE_SESSIONS = 32


class LanAccessManager:
    """Manage one-run pairing secrets and persistent device sessions.

    The pairing token is regenerated on every application start. Browsers never
    receive it as their long-lived credential; a successful pair gets a separate
    random device-session cookie whose SHA-256 digest is stored locally.
    """

    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._sessions_path = self.root / SESSIONS_FILE
        self._pairing_path = self.root / PAIRING_FILE
        self._migrate_legacy_access_token()
        self.pairing_token = self._new_pairing_token()

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _chmod_private(path: Path) -> None:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _atomic_write(self, path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        self._chmod_private(temporary)
        os.replace(temporary, path)
        self._chmod_private(path)

    def _read_session_hashes(self) -> list[str]:
        try:
            payload = json.loads(self._sessions_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return []
        rows = payload.get("session_hashes") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        out: list[str] = []
        for value in rows:
            text = str(value or "").strip().lower()
            if len(text) == 64 and all(char in "0123456789abcdef" for char in text):
                if text not in out:
                    out.append(text)
        return out[-MAX_DEVICE_SESSIONS:]

    def _write_session_hashes(self, hashes: list[str]) -> None:
        payload = {
            "version": 1,
            "session_hashes": list(dict.fromkeys(hashes))[-MAX_DEVICE_SESSIONS:],
        }
        self._atomic_write(
            self._sessions_path,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        )

    def _migrate_legacy_access_token(self) -> None:
        legacy = self.root / LEGACY_ACCESS_FILE
        try:
            value = legacy.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if len(value) >= 24:
            with self._lock:
                hashes = self._read_session_hashes()
                hashes.append(self._digest(value))
                self._write_session_hashes(hashes)
        try:
            legacy.unlink()
        except OSError:
            pass

    def _new_pairing_token(self) -> str:
        token = secrets.token_urlsafe(32)
        self._atomic_write(self._pairing_path, token + "\n")
        return token

    def valid_pairing_token(self, supplied: str | None) -> bool:
        value = str(supplied or "")
        return bool(value) and hmac.compare_digest(value, self.pairing_token)

    def issue_device_session(self) -> str:
        token = secrets.token_urlsafe(32)
        digest = self._digest(token)
        with self._lock:
            hashes = self._read_session_hashes()
            hashes.append(digest)
            self._write_session_hashes(hashes)
        return token

    def authorized(self, supplied: str | None) -> bool:
        value = str(supplied or "")
        if not value:
            return False
        digest = self._digest(value)
        with self._lock:
            hashes = self._read_session_hashes()
        return any(hmac.compare_digest(digest, stored) for stored in hashes)
