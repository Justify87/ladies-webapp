from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import desc, select

from ladies_v9.models import PersonUserState, utcnow

from .models import ChatMessage, ChatThread, ContactEndpoint
from .service import (
    MEDIA_TYPES,
    MessagingService as _BaseMessagingService,
    _load,
    jid_phone,
)

LOGGER = logging.getLogger("ladies_explorer.messaging.service_v2")


class MessagingService(_BaseMessagingService):
    """Canonical conversation service.

    Conversation existence is derived only from persisted messaging data.
    Person/listing/user-state data may enrich a conversation, but it must never
    decide whether a thread or message exists or is visible.
    """

    def __init__(self, database, data_dir):
        self._sync_status: dict[str, Any] = {
            "state": "IDLE",
            "progress": None,
            "is_latest": None,
        }
        super().__init__(database, data_dir)

    def status(self) -> dict[str, Any]:
        payload = super().status()
        payload["sync"] = dict(self._sync_status)
        return payload

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

    @staticmethod
    def _endpoint_belongs_to_thread(db, thread: ChatThread, endpoint: ContactEndpoint) -> bool:
        if endpoint.state != "ACTIVE":
            return False
        if thread.person_id is not None:
            return endpoint.person_id == thread.person_id
        if endpoint.person_id is not None:
            return False
        return db.scalar(
            select(ChatMessage.message_id)
            .where(
                ChatMessage.thread_id == thread.thread_id,
                ChatMessage.endpoint_id == endpoint.endpoint_id,
            )
            .limit(1)
        ) is not None

    @classmethod
    def _resolved_endpoint(cls, db, thread: ChatThread, endpoint_id: int | None, chooser) -> ContactEndpoint:
        if endpoint_id:
            endpoint = db.get(ContactEndpoint, int(endpoint_id))
        elif thread.person_id is not None:
            endpoint = chooser(db, thread.person_id)
        else:
            endpoint = db.scalar(
                select(ContactEndpoint)
                .join(ChatMessage, ChatMessage.endpoint_id == ContactEndpoint.endpoint_id)
                .where(
                    ChatMessage.thread_id == thread.thread_id,
                    ContactEndpoint.person_id.is_(None),
                    ContactEndpoint.state == "ACTIVE",
                )
                .order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id))
                .limit(1)
            )
        if endpoint is None or not cls._endpoint_belongs_to_thread(db, thread, endpoint):
            raise ValueError("Kein gültiger WhatsApp-Endpunkt ausgewählt")
        return endpoint

    def thread(self, thread_id: int, limit: int = 100, before_message_id: int | None = None) -> dict[str, Any]:
        payload = super().thread(thread_id, limit, before_message_id)
        if payload.get("person_id") is not None:
            return payload
        with self.database.session() as db:
            endpoint_ids = db.scalars(
                select(ChatMessage.endpoint_id)
                .where(
                    ChatMessage.thread_id == int(thread_id),
                    ChatMessage.endpoint_id.is_not(None),
                )
                .distinct()
            ).all()
            endpoints = [
                db.get(ContactEndpoint, int(endpoint_id))
                for endpoint_id in endpoint_ids
                if endpoint_id is not None
            ]
            endpoints = [item for item in endpoints if item is not None]
            payload["endpoints"] = [self._endpoint_payload(item) for item in endpoints]
        return payload

    def subscribe_presence(self, thread_id: int) -> None:
        if self.gateway.state != "CONNECTED":
            return
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                return
            try:
                endpoint = self._resolved_endpoint(db, thread, None, self._choose_endpoint)
            except ValueError:
                return
            jid = self._endpoint_jid(endpoint)
        try:
            self.gateway.command("subscribe_presence", jid=jid, timeout=5)
        except Exception:
            LOGGER.debug("Presence subscription failed thread_id=%s", thread_id, exc_info=True)

    def _dispatch_text(self, message_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None or message.direction != "OUT":
                raise KeyError(message_id)
            endpoint = db.get(ContactEndpoint, message.endpoint_id) if message.endpoint_id else None
            thread = db.get(ChatThread, message.thread_id)
            if thread is None or endpoint is None or not self._endpoint_belongs_to_thread(db, thread, endpoint):
                raise ValueError("Kein gültiger WhatsApp-Endpunkt ausgewählt")
            jid = self._endpoint_jid(endpoint)
            endpoint.provider_address = jid
            text = message.text or ""
            quote = self._quote_command(db, message)
        try:
            response = self.gateway.command(
                "send_text",
                jid=jid,
                text=text,
                quote=quote,
                client_message_id=str(message_id),
                timeout=30,
            )
            return self._save_send_result(message_id, response)
        except Exception as exc:
            self._mark_failed(message_id, exc)
            raise

    def _dispatch_media(self, message_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None or message.direction != "OUT" or message.message_type not in MEDIA_TYPES:
                raise KeyError(message_id)
            endpoint = db.get(ContactEndpoint, message.endpoint_id) if message.endpoint_id else None
            thread = db.get(ChatThread, message.thread_id)
            if thread is None or endpoint is None or not self._endpoint_belongs_to_thread(db, thread, endpoint):
                raise ValueError("Kein gültiger WhatsApp-Endpunkt ausgewählt")
            jid = self._endpoint_jid(endpoint)
            endpoint.provider_address = jid
            media = _load(message.media_json, {}) or {}
            file_path = self.media_store.resolve(self.media_store.archive_name(media))
            meta = self._ensure_meta(db, message)
            quote = self._quote_command(db, message)
            command = {
                "jid": jid,
                "media_type": message.message_type,
                "file_path": str(file_path),
                "mime_type": media.get("mime_type"),
                "file_name": media.get("file_name"),
                "caption": message.text or media.get("caption"),
                "view_once": bool(meta.view_once),
                "quote": quote,
                "client_message_id": str(message_id),
            }
        try:
            response = self.gateway.command("send_media", timeout=90, **command)
            return self._save_send_result(message_id, response)
        except Exception as exc:
            self._mark_failed(message_id, exc)
            raise

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
            self._sync_status = {
                "state": str(event.get("state") or "UNKNOWN").upper(),
                "progress": event.get("progress"),
                "is_latest": event.get("is_latest"),
            }
            self.events.publish({"type": "sync", **self._sync_status})
            return
        super()._worker_event(event)


__all__ = ["MessagingService", "jid_phone"]
