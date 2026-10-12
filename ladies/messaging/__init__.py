from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path

from sqlalchemy import desc, func, select

from ladies_v9.models import PersonUserState
from .models import ChatMessage, ChatThread, ContactEndpoint
from .service import jid_phone
from . import service_runtime as _service_runtime

# Keep legacy import paths working while all physical source paths remain neutral.
sys.modules.setdefault(__name__ + ".service_v92", _service_runtime)

from . import service_reviewfix as _service_reviewfix


LOGGER = logging.getLogger("ladies_explorer.messaging.rehydrate")
REHYDRATE_LIMIT = 10


class MessagingService(_service_reviewfix.MessagingService):
    """Current messaging service plus user-facing thread visibility rules."""

    def __init__(self, db_path, data_dir):
        # The small sidecar deliberately lives with the WhatsApp session rather
        # than inside the replaceable application DB.  It only retains the last
        # ten already-observed messages per address and therefore lets a fresh DB
        # recover a little conversation context after a profile is liked.
        self._rehydrate_cache_path = Path(data_dir) / "whatsapp" / "recent-history.json"
        self._rehydrate_cache_lock = threading.RLock()
        super().__init__(db_path, data_dir)

    def subscribe_presence(self, thread_id: int) -> None:
        # Opening/refreshing a thread can happen while Baileys is reconnecting.
        # Presence is supplementary, so do not turn a transient disconnect into
        # repeated worker warnings or visible chat instability.
        if self.worker.state != "CONNECTED":
            return
        return super().subscribe_presence(thread_id)

    @staticmethod
    def _history_payload_keys(payload: dict) -> list[str]:
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
                phone = jid_phone(value)
                if phone:
                    key = f"phone:{phone}"
                    if key not in keys:
                        keys.append(key)
            key = f"jid:{value}"
            if key not in keys:
                keys.append(key)
        return keys

    @staticmethod
    def _history_endpoint_keys(endpoint: ContactEndpoint) -> list[str]:
        keys: list[str] = []
        if endpoint.normalized_phone:
            keys.append(f"phone:{endpoint.normalized_phone}")
            keys.append(f"jid:{endpoint.normalized_phone.lstrip('+')}@s.whatsapp.net")
        if endpoint.provider_address:
            keys.append(f"jid:{endpoint.provider_address}")
            if endpoint.provider_address.endswith("@s.whatsapp.net"):
                phone = jid_phone(endpoint.provider_address)
                if phone:
                    keys.append(f"phone:{phone}")
        return list(dict.fromkeys(keys))

    def _read_history_cache_unlocked(self) -> dict:
        try:
            data = json.loads(self._rehydrate_cache_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("conversations"), dict):
                return data
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        return {"version": 1, "conversations": {}}

    def _write_history_cache_unlocked(self, data: dict) -> None:
        self._rehydrate_cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._rehydrate_cache_path.with_name(
            f".{self._rehydrate_cache_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self._rehydrate_cache_path)
        try:
            os.chmod(self._rehydrate_cache_path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _clean_history_payload(payload: dict) -> dict:
        clean = dict(payload)
        clean.pop("rehydrated", None)
        media = clean.get("media")
        if isinstance(media, dict):
            clean["media"] = {key: value for key, value in media.items() if key != "data_base64"}
        return clean

    def _cache_history_payloads(self, payloads) -> None:
        usable = [
            self._clean_history_payload(payload)
            for payload in payloads
            if isinstance(payload, dict) and payload.get("provider_message_id")
        ]
        if not usable:
            return
        with self._rehydrate_cache_lock:
            data = self._read_history_cache_unlocked()
            conversations = data.setdefault("conversations", {})
            changed = False
            for payload in usable:
                provider_id = str(payload["provider_message_id"])
                for key in self._history_payload_keys(payload):
                    rows = conversations.get(key)
                    if not isinstance(rows, list):
                        rows = []
                    rows = [row for row in rows if isinstance(row, dict) and str(row.get("provider_message_id")) != provider_id]
                    rows.append(payload)
                    rows.sort(key=lambda row: (float(row.get("timestamp") or 0), str(row.get("provider_message_id") or "")))
                    conversations[key] = rows[-REHYDRATE_LIMIT:]
                    changed = True
            if changed:
                self._write_history_cache_unlocked(data)

    def _cached_history_for_endpoint(self, endpoint: ContactEndpoint) -> list[dict]:
        with self._rehydrate_cache_lock:
            data = self._read_history_cache_unlocked()
        conversations = data.get("conversations") or {}
        by_id: dict[str, dict] = {}
        for key in self._history_endpoint_keys(endpoint):
            for payload in conversations.get(key) or []:
                if not isinstance(payload, dict) or not payload.get("provider_message_id"):
                    continue
                by_id[str(payload["provider_message_id"])] = payload
        return sorted(
            by_id.values(),
            key=lambda row: (float(row.get("timestamp") or 0), str(row.get("provider_message_id") or "")),
            reverse=True,
        )

    def _person_message_count(self, person_id: int) -> int:
        with self.database.session() as db:
            thread = db.scalar(select(ChatThread).where(ChatThread.person_id == int(person_id)))
            if thread is None:
                return 0
            return int(db.scalar(
                select(func.count(ChatMessage.message_id)).where(
                    ChatMessage.thread_id == thread.thread_id,
                    ChatMessage.direction.in_(("IN", "OUT")),
                )
            ) or 0)

    def _ingest_message(self, payload: dict) -> None:
        provider_id = payload.get("provider_message_id")
        existed = False
        if provider_id:
            with self.database.session() as db:
                existed = db.scalar(select(ChatMessage.message_id).where(
                    ChatMessage.provider_message_id == str(provider_id)
                )) is not None
        self._cache_history_payloads([payload])
        super()._ingest_message(payload)

        # Restored history is context, not unread activity.  The runtime ingest
        # path intentionally remains shared with live messages, so correct the
        # unread counter only for a genuinely inserted historical inbound item.
        if payload.get("rehydrated") and not payload.get("from_me") and provider_id and not existed:
            with self.database.session() as db:
                message = db.scalar(select(ChatMessage).where(
                    ChatMessage.provider_message_id == str(provider_id)
                ))
                if message is not None:
                    thread = db.get(ChatThread, int(message.thread_id))
                    if thread is not None:
                        thread.unread_count = max(0, int(thread.unread_count or 0) - 1)
                        latest = db.scalar(select(func.max(ChatMessage.timestamp)).where(
                            ChatMessage.thread_id == thread.thread_id
                        ))
                        thread.last_message_at = latest

    def _worker_event(self, event: dict) -> None:
        if event.get("type") == "history_seed":
            self._cache_history_payloads(event.get("payloads") or [])
            return
        return super()._worker_event(event)

    def rehydrate_recent_history(self, person_id: int, limit: int = REHYDRATE_LIMIT) -> dict:
        """Best-effort recovery of recent context after the replaceable DB was reset.

        The persisted WhatsApp sidecar is replayed first.  If fewer than ``limit``
        messages are available and an anchor exists, Baileys is asked for a small
        ON_DEMAND history sync.  ``syncFullHistory`` stays disabled.
        """
        person_id = int(person_id)
        limit = max(1, min(REHYDRATE_LIMIT, int(limit)))
        self.sync_person(person_id)
        existing = self._person_message_count(person_id)
        if existing >= limit:
            return {"person_id": person_id, "existing": existing, "restored": 0, "requested": 0}

        with self.database.session() as db:
            endpoints = db.scalars(
                select(ContactEndpoint)
                .where(ContactEndpoint.person_id == person_id, ContactEndpoint.state == "ACTIVE")
                .order_by(desc(ContactEndpoint.is_preferred), desc(ContactEndpoint.last_seen_at))
            ).all()

        restored = 0
        requested = 0
        for endpoint in endpoints:
            missing = max(0, limit - self._person_message_count(person_id))
            if not missing:
                break
            cached = self._cached_history_for_endpoint(endpoint)
            if not cached:
                continue

            for payload in reversed(cached[:missing]):
                before = self._person_message_count(person_id)
                historical = dict(payload)
                historical["rehydrated"] = True
                self._ingest_message(historical)
                after = self._person_message_count(person_id)
                if after > before:
                    restored += after - before

            missing = max(0, limit - self._person_message_count(person_id))
            if not missing or self.worker.state != "CONNECTED":
                continue

            anchor = next((row for row in reversed(cached) if isinstance(row.get("provider_key"), dict) and row["provider_key"].get("id")), None)
            if anchor is None:
                continue
            jid = endpoint.provider_address
            if not jid and endpoint.normalized_phone:
                jid = endpoint.normalized_phone.lstrip("+") + "@s.whatsapp.net"
            if not jid:
                continue
            try:
                timestamp_ms = int(float(anchor.get("timestamp") or 0) * 1000)
                if timestamp_ms <= 0:
                    continue
                self.worker.command(
                    "fetch_recent_history",
                    jid=jid,
                    count=missing,
                    oldest_key=anchor["provider_key"],
                    oldest_timestamp_ms=timestamp_ms,
                    timeout=10,
                )
                requested += missing
            except Exception as exc:
                # WhatsApp currently does not guarantee an ON_DEMAND response for
                # every linked-device session.  A failed request must never make
                # liking a profile fail or remove the cached messages already restored.
                LOGGER.info("Recent WhatsApp history request skipped person_id=%s endpoint_id=%s: %s", person_id, endpoint.endpoint_id, exc)

        return {
            "person_id": person_id,
            "existing": self._person_message_count(person_id),
            "restored": restored,
            "requested": requested,
        }

    def threads(self, filter_name: str = "ALL"):
        if filter_name == "UNASSIGNED":
            return super().threads(filter_name)

        with self.database.session() as db:
            rows = db.scalars(
                select(ChatThread).order_by(
                    desc(ChatThread.last_message_at),
                    desc(ChatThread.updated_at),
                )
            ).all()
            out = []
            for thread in rows:
                if thread.person_id is None:
                    continue

                last = db.scalar(
                    select(ChatMessage)
                    .where(ChatMessage.thread_id == thread.thread_id)
                    .order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id))
                    .limit(1)
                )
                # Only empty placeholder threads stay hidden. Any real 1:1
                # conversation belongs in the inbox, including a neutral person
                # who has written first and has not yet been liked/favorited.
                if last is None:
                    continue
                if filter_name == "UNREAD" and not thread.unread_count:
                    continue

                state = db.get(PersonUserState, int(thread.person_id))
                payload = self._thread_payload(db, thread, last)
                payload["favorite"] = bool(state.favorite) if state else False
                payload["preference"] = state.preference if state else None
                out.append(payload)

            # Keep newest-message order inside each stable group while pinning
            # favorites first, then likes, then neutral conversations.
            out.sort(key=lambda item: (
                0 if item.get("favorite") else 1,
                0 if item.get("preference") == "LIKE" else 1,
            ))
            return out


from .web_reviewfix import install_messaging

__all__ = ["MessagingService", "install_messaging"]
