from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import desc, select

from ladies_v9.models import PersonUserState, utcnow

from .models import ChatMessage, ChatThread
from .service import MessagingService as _BaseMessagingService
from .service import jid_phone

LOGGER = logging.getLogger("ladies_explorer.messaging.service_v2")


class MessagingService(_BaseMessagingService):
    """Canonical conversation service.

    Conversation existence is derived only from persisted messaging data.
    Person/listing/user-state data may enrich a conversation, but it must never
    decide whether a thread or message exists or is visible.
    """

    def threads(self, filter_name: str = "ALL") -> list[dict[str, Any]]:
        filter_name = str(filter_name or "ALL").upper()
        if filter_name not in {"ALL", "UNREAD", "UNASSIGNED"}:
            raise ValueError("Ungültiger Nachrichtenfilter")

        with self.database.session() as db:
            query = select(ChatThread).order_by(
                desc(ChatThread.last_message_at),
                desc(ChatThread.updated_at),
                desc(ChatThread.thread_id),
            )
            if filter_name == "UNASSIGNED":
                query = query.where(ChatThread.person_id.is_(None))
            elif filter_name == "UNREAD":
                query = query.where(ChatThread.unread_count > 0)

            rows = db.scalars(query).all()
            items: list[dict[str, Any]] = []
            for thread in rows:
                last = db.scalar(
                    select(ChatMessage)
                    .where(ChatMessage.thread_id == thread.thread_id)
                    .order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id))
                    .limit(1)
                )
                if last is None:
                    continue
                payload = self._thread_payload(db, thread, last)

                # Profile state is optional UI enrichment only. It never
                # participates in thread selection, persistence, assignment,
                # history sync, or message ingest.
                if thread.person_id is not None:
                    state = db.get(PersonUserState, int(thread.person_id))
                    payload["favorite"] = bool(state.favorite) if state else False
                else:
                    payload["favorite"] = False
                items.append(payload)
            return items

    @staticmethod
    def _message_payload(message: ChatMessage | None) -> dict[str, Any] | None:
        payload = _BaseMessagingService._message_payload(message)
        if payload is None or message is None:
            return payload
        payload["channel"] = (
            "INTERNAL"
            if message.direction == "SYSTEM" or message.endpoint_id is None
            else "WHATSAPP"
        )
        return payload

    def add_internal_message(
        self,
        thread_id: int,
        text: str,
        *,
        message_type: str = "INTERNAL",
    ) -> dict[str, Any]:
        """Persist a Ladies-only timeline entry without touching WhatsApp."""
        text = str(text or "").strip()
        if not text:
            raise ValueError("Interne Nachricht darf nicht leer sein")
        kind = str(message_type or "INTERNAL").strip().upper()[:24] or "INTERNAL"

        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            now = utcnow()
            message = ChatMessage(
                thread_id=thread.thread_id,
                endpoint_id=None,
                provider_message_id=None,
                direction="SYSTEM",
                message_type=kind,
                text=text,
                timestamp=now,
                status="RECEIVED",
            )
            db.add(message)
            db.flush()
            thread.last_message_at = now
            thread.updated_at = now
            payload = self._message_payload(message)
            message_id = int(message.message_id)

        self.events.publish(
            {"type": "message", "thread_id": int(thread_id), "message_id": message_id, "channel": "INTERNAL"}
        )
        return payload or {}

    def _worker_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "history_batch":
            ingested = 0
            for payload in event.get("payloads") or []:
                if not isinstance(payload, dict):
                    continue
                try:
                    self._ingest_message(payload)
                    ingested += 1
                except Exception:
                    LOGGER.exception("Failed to ingest WhatsApp history message")
            if ingested:
                self.events.publish({"type": "history_batch", "count": ingested})
            return
        if event_type == "sync":
            self.events.publish(
                {
                    "type": "sync",
                    "state": str(event.get("state") or "UNKNOWN").upper(),
                    "progress": event.get("progress"),
                    "is_latest": event.get("is_latest"),
                }
            )
            return
        super()._worker_event(event)


__all__ = ["MessagingService", "jid_phone"]
