from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from ladies_v9.identity import normalize_phone_number
from .models import ContactEndpoint

REHYDRATE_LIMIT = 10
LOCAL_MEDIA_KEYS = {"archive_name", "sha256", "private_view_once", "local_url"}


def _jid_phone(jid: str | None) -> str | None:
    if not jid:
        return None
    local = str(jid).split("@", 1)[0].split(":", 1)[0]
    return normalize_phone_number(local)


class RecentHistoryCache:
    """Bounded fallback cache for recently observed live WhatsApp messages.

    The linked-device session intentionally outlives a replaceable application
    database. Keeping a tiny live-message fallback beside that session can seed a
    rebuilt database when provider history is temporarily unavailable. Provider
    history/backfill itself belongs only in the canonical SQL conversation store.
    """

    def __init__(self, data_dir: str | Path, limit: int = REHYDRATE_LIMIT):
        self.path = Path(data_dir) / "whatsapp" / "recent-history.json"
        self.limit = max(1, min(REHYDRATE_LIMIT, int(limit)))
        self._lock = threading.RLock()

    @staticmethod
    def _payload_keys(payload: dict[str, Any]) -> list[str]:
        provider_key = payload.get("provider_key") if isinstance(payload.get("provider_key"), dict) else {}
        candidates = [
            payload.get("phone_jid"),
            payload.get("jid"),
            provider_key.get("remoteJidAlt"),
            provider_key.get("remoteJid"),
        ]
        keys: list[str] = []
        for raw in candidates:
            value = str(raw or "").strip()
            if not value:
                continue
            if value.endswith("@s.whatsapp.net"):
                phone = _jid_phone(value)
                if phone:
                    keys.append(f"phone:{phone}")
            keys.append(f"jid:{value}")
        return list(dict.fromkeys(keys))

    @staticmethod
    def _endpoint_keys(endpoint: ContactEndpoint) -> list[str]:
        keys: list[str] = []
        if endpoint.normalized_phone:
            keys.append(f"phone:{endpoint.normalized_phone}")
            keys.append(f"jid:{endpoint.normalized_phone.lstrip('+')}@s.whatsapp.net")
        if endpoint.provider_address:
            keys.append(f"jid:{endpoint.provider_address}")
            if endpoint.provider_address.endswith("@s.whatsapp.net"):
                phone = _jid_phone(endpoint.provider_address)
                if phone:
                    keys.append(f"phone:{phone}")
        return list(dict.fromkeys(keys))

    @staticmethod
    def _clean(payload: dict[str, Any]) -> dict[str, Any]:
        clean = dict(payload)
        clean.pop("rehydrated", None)
        media = clean.get("media")
        if isinstance(media, dict):
            clean["media"] = {key: value for key, value in media.items() if key != "data_base64"}
        return clean

    @staticmethod
    def _merge_payload(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = dict(existing)
        merged.update(incoming)

        existing_media = existing.get("media") if isinstance(existing.get("media"), dict) else None
        incoming_media = incoming.get("media") if isinstance(incoming.get("media"), dict) else None
        if incoming_media is None:
            if existing_media is not None:
                merged["media"] = dict(existing_media)
            return merged

        media = dict(existing_media or {})
        for key, value in incoming_media.items():
            previous = media.get(key)
            if (
                key in LOCAL_MEDIA_KEYS
                and (value is None or value == "")
                and previous is not None
                and previous != ""
            ):
                continue
            media[key] = value
        merged["media"] = media
        return merged

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("conversations"), dict):
                return data
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        return {"version": 1, "conversations": {}}

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def store(self, payloads) -> None:
        usable = [
            self._clean(payload)
            for payload in payloads
            if (
                isinstance(payload, dict)
                and payload.get("provider_message_id")
                and not payload.get("rehydrated")
            )
        ]
        if not usable:
            return
        with self._lock:
            data = self._read_unlocked()
            conversations = data.setdefault("conversations", {})
            for payload in usable:
                provider_id = str(payload["provider_message_id"])
                keys = self._payload_keys(payload)
                merged_payload = payload
                for key in keys:
                    rows = conversations.get(key)
                    if not isinstance(rows, list):
                        continue
                    for row in rows:
                        if isinstance(row, dict) and str(row.get("provider_message_id")) == provider_id:
                            merged_payload = self._merge_payload(row, merged_payload)

                for key in keys:
                    rows = conversations.get(key)
                    if not isinstance(rows, list):
                        rows = []
                    rows = [
                        row for row in rows
                        if isinstance(row, dict) and str(row.get("provider_message_id")) != provider_id
                    ]
                    rows.append(merged_payload)
                    rows.sort(key=lambda row: (float(row.get("timestamp") or 0), str(row.get("provider_message_id") or "")))
                    conversations[key] = rows[-self.limit:]
            self._write_unlocked(data)

    def for_endpoint(self, endpoint: ContactEndpoint) -> list[dict[str, Any]]:
        with self._lock:
            conversations = (self._read_unlocked().get("conversations") or {})
        by_id: dict[str, dict[str, Any]] = {}
        for key in self._endpoint_keys(endpoint):
            for payload in conversations.get(key) or []:
                if isinstance(payload, dict) and payload.get("provider_message_id"):
                    provider_id = str(payload["provider_message_id"])
                    existing = by_id.get(provider_id)
                    by_id[provider_id] = self._merge_payload(existing, payload) if existing else payload
        return sorted(
            by_id.values(),
            key=lambda row: (float(row.get("timestamp") or 0), str(row.get("provider_message_id") or "")),
            reverse=True,
        )
