from __future__ import annotations

import logging
import sys

from sqlalchemy import desc, func, select

from ladies_v9.models import PersonUserState
from .history_cache import RecentHistoryCache
from .models import ChatMessage, ChatThread, ContactEndpoint
from .service import jid_phone
from . import service_runtime as _service_runtime

# Temporary compatibility until the canonical messaging service replaces the
# historical runtime/complete/reviewfix stack later in this refactor.
sys.modules.setdefault(__name__ + ".service_v92", _service_runtime)

from . import service_reviewfix as _service_reviewfix


LOGGER = logging.getLogger("ladies_explorer.messaging.rehydrate")
REHYDRATE_LIMIT = 10


class MessagingService(_service_reviewfix.MessagingService):
    """Current messaging service plus thread visibility and recent rehydration."""

    def __init__(self, db_path, data_dir):
        self.history_cache = RecentHistoryCache(data_dir, limit=REHYDRATE_LIMIT)
        super().__init__(db_path, data_dir)

    def subscribe_presence(self, thread_id: int) -> None:
        # Opening/refreshing a thread can happen while Baileys is reconnecting.
        # Presence is supplementary, so do not turn a transient disconnect into
        # repeated worker warnings or visible chat instability.
        if self.worker.state != "CONNECTED":
            return
        return super().subscribe_presence(thread_id)

    # Keep these tiny adapters during the staged refactor so callers/tests that
    # exercise message ingest do not need to know how the cache is persisted.
    def _cache_history_payloads(self, payloads) -> None:
        self.history_cache.store(payloads)

    def _cached_history_for_endpoint(self, endpoint: ContactEndpoint) -> list[dict]:
        return self.history_cache.for_endpoint(endpoint)

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

        # Restored history is context, not unread activity. The runtime ingest
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

        The persisted WhatsApp sidecar is replayed first. If fewer than ``limit``
        messages are available and an anchor exists, Baileys is asked for a small
        ON_DEMAND history sync. ``syncFullHistory`` stays disabled.
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

            anchor = next((
                row for row in reversed(cached)
                if isinstance(row.get("provider_key"), dict)
                and row["provider_key"].get("id")
            ), None)
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
                # WhatsApp does not guarantee an ON_DEMAND response for every
                # linked-device session. Cached restoration remains successful.
                LOGGER.info(
                    "Recent WhatsApp history request skipped person_id=%s endpoint_id=%s: %s",
                    person_id,
                    endpoint.endpoint_id,
                    exc,
                )

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
                if last is None:
                    continue
                if filter_name == "UNREAD" and not thread.unread_count:
                    continue

                state = db.get(PersonUserState, int(thread.person_id))
                payload = self._thread_payload(db, thread, last)
                payload["favorite"] = bool(state.favorite) if state else False
                payload["preference"] = state.preference if state else None
                out.append(payload)

            out.sort(key=lambda item: (
                0 if item.get("favorite") else 1,
                0 if item.get("preference") == "LIKE" else 1,
            ))
            return out


from .web_reviewfix import install_messaging

__all__ = ["MessagingService", "install_messaging"]
