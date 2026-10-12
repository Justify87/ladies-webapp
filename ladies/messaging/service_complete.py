from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select

from ladies_v9.models import utcnow
from .models import ChatMessage, ChatMessageMeta, ChatReaction, ChatThread, ContactEndpoint
from .service import MessagingService as LegacyMessagingService
from .service_runtime import MessagingService as RuntimeMessagingService

LOGGER = logging.getLogger("ladies_explorer.messaging.complete")
STATUS_RANK = {
    "PENDING": 0,
    "FAILED": 0,
    "SENT": 1,
    "DELIVERED": 2,
    "READ": 3,
    "PLAYED": 4,
    "RECEIVED": 1,
    "DELETED": 5,
}
MEDIA_TYPES = {"IMAGE", "VIDEO", "AUDIO", "VOICE", "DOCUMENT"}
VIEW_ONCE_TYPES = {"IMAGE", "VIDEO", "AUDIO", "VOICE"}


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: str | None, fallback=None):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _utc_from_ms(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, timezone.utc)
    except (TypeError, ValueError, OSError):
        return utcnow()


class MessagingService(RuntimeMessagingService):
    """Additive 1:1 WhatsApp feature layer on top of the stable V9 runtime."""

    def _archive_incoming_media(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Keep compatibility with older worker payloads that carried base64.
        payload = super()._archive_incoming_media(payload)
        media = payload.get("media")
        if not isinstance(media, dict):
            return payload
        archive_name = media.get("archive_name")
        if not archive_name:
            return payload
        name = Path(str(archive_name)).name
        target = (self.media_dir / name).resolve()
        try:
            target.relative_to(self.media_dir.resolve())
        except ValueError:
            LOGGER.warning("Rejected media path outside archive: %s", archive_name)
            media.pop("archive_name", None)
            return payload
        if target.exists():
            media = dict(media)
            media["archive_name"] = name
            media["local_url"] = f"/archive/whatsapp/media/{name}"
            payload = dict(payload)
            payload["media"] = media
        return payload

    @staticmethod
    def _ensure_meta(db, message: ChatMessage) -> ChatMessageMeta:
        meta = db.get(ChatMessageMeta, int(message.message_id))
        if meta is None:
            meta = ChatMessageMeta(message_id=message.message_id)
            db.add(meta)
            db.flush()
        return meta

    @staticmethod
    def _provider_key(message: ChatMessage) -> dict[str, Any] | None:
        return _load(message.extra.provider_key_json, None) if message.extra else None

    @staticmethod
    def _endpoint_jid(endpoint: ContactEndpoint | None) -> str:
        if endpoint is None or endpoint.state != "ACTIVE":
            raise ValueError("Kein gültiger WhatsApp-Endpunkt ausgewählt")
        if endpoint.provider_address:
            return endpoint.provider_address
        if endpoint.normalized_phone:
            return endpoint.normalized_phone.lstrip("+") + "@s.whatsapp.net"
        raise ValueError("Für diesen WhatsApp-Endpunkt ist keine sendefähige Adresse bekannt")

    def _quote_command(self, db, message: ChatMessage) -> dict[str, Any] | None:
        if not message.reply_to_message_id:
            return None
        quoted = db.get(ChatMessage, int(message.reply_to_message_id))
        if quoted is None:
            return None
        key = self._provider_key(quoted)
        if not key:
            return None
        media = _load(quoted.media_json, {}) or {}
        return {
            "key": key,
            "message_type": quoted.message_type,
            "text": quoted.text,
            "caption": media.get("caption"),
            "file_name": media.get("file_name"),
        }

    def _save_send_result(self, message_id: int, response: dict[str, Any]) -> dict[str, Any]:
        payload = response.get("payload") or {}
        provider_id = payload.get("provider_message_id")
        if not provider_id:
            raise RuntimeError("WhatsApp lieferte keine Nachrichten-ID")
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None:
                raise KeyError(message_id)
            duplicate = db.scalar(select(ChatMessage).where(
                ChatMessage.provider_message_id == provider_id,
                ChatMessage.message_id != message.message_id,
            ))
            if duplicate is not None and duplicate.direction == "OUT" and duplicate.thread_id == message.thread_id:
                if STATUS_RANK.get(duplicate.status, 0) > STATUS_RANK.get(message.status, 0):
                    message.status = duplicate.status
                db.delete(duplicate)
                db.flush()
            elif duplicate is not None:
                raise RuntimeError("Provider-Nachrichten-ID ist bereits anderweitig belegt")
            message.provider_message_id = provider_id
            if STATUS_RANK.get(message.status, 0) < STATUS_RANK["SENT"]:
                message.status = "SENT"
            message.error = None
            meta = self._ensure_meta(db, message)
            if payload.get("provider_key"):
                meta.provider_key_json = _json(payload["provider_key"])
                meta.updated_at = utcnow()
            thread = db.get(ChatThread, message.thread_id)
            if thread:
                thread.last_message_at = message.timestamp
                thread.updated_at = utcnow()
        result = self.message(int(message_id))
        self.events.publish({
            "type": "message_update",
            "thread_id": result["thread_id"],
            "message_id": int(message_id),
            "status": result["status"],
        })
        return result

    def _mark_failed(self, message_id: int, exc: Exception) -> None:
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message:
                message.status = "FAILED"
                message.error = str(exc)[:1000]
        self.events.publish({"type": "message_update", "message_id": int(message_id), "status": "FAILED"})

    def _dispatch_text(self, message_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None or message.direction != "OUT":
                raise KeyError(message_id)
            endpoint = db.get(ContactEndpoint, message.endpoint_id) if message.endpoint_id else None
            thread = db.get(ChatThread, message.thread_id)
            if thread is None or thread.person_id is None or endpoint is None or endpoint.person_id != thread.person_id:
                raise ValueError("Kein gültiger WhatsApp-Endpunkt ausgewählt")
            jid = self._endpoint_jid(endpoint)
            endpoint.provider_address = jid
            text = message.text or ""
            quote = self._quote_command(db, message)
        try:
            response = self.worker.command(
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
            if thread is None or thread.person_id is None or endpoint is None or endpoint.person_id != thread.person_id:
                raise ValueError("Kein gültiger WhatsApp-Endpunkt ausgewählt")
            jid = self._endpoint_jid(endpoint)
            endpoint.provider_address = jid
            media = _load(message.media_json, {}) or {}
            name = Path(str(media.get("archive_name") or "")).name
            file_path = (self.media_dir / name).resolve()
            if not name or not file_path.exists():
                raise ValueError("Lokale Mediendatei fehlt")
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
            response = self.worker.command("send_media", timeout=90, **command)
            return self._save_send_result(message_id, response)
        except Exception as exc:
            self._mark_failed(message_id, exc)
            raise

    def send_media(
        self,
        thread_id: int,
        *,
        archive_name: str,
        media_type: str,
        mime_type: str | None = None,
        file_name: str | None = None,
        caption: str | None = None,
        endpoint_id: int | None = None,
        reply_to: int | None = None,
        view_once: bool = False,
    ) -> dict[str, Any]:
        media_type = str(media_type or "").upper()
        if media_type not in MEDIA_TYPES:
            raise ValueError("Nicht unterstützter Medientyp")
        if view_once and media_type not in VIEW_ONCE_TYPES:
            raise ValueError("Einmal ansehen ist für diesen Medientyp nicht verfügbar")
        name = Path(str(archive_name or "")).name
        target = (self.media_dir / name).resolve()
        if not name or not target.exists():
            raise ValueError("Mediendatei fehlt")
        caption = str(caption or "").strip() or None
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            endpoint = db.get(ContactEndpoint, int(endpoint_id)) if endpoint_id else self._choose_endpoint(db, thread.person_id)
            if endpoint is None or thread.person_id is None or endpoint.person_id != thread.person_id:
                raise ValueError("Kein gültiger WhatsApp-Endpunkt ausgewählt")
            if reply_to is not None:
                quoted = db.get(ChatMessage, int(reply_to))
                if quoted is None or quoted.thread_id != thread.thread_id:
                    raise ValueError("Antwort-Ziel gehört nicht zu diesem Chat")
            media = {
                "archive_name": name,
                "local_url": f"/archive/whatsapp/media/{name}",
                "mime_type": mime_type or "application/octet-stream",
                "file_name": file_name,
                "caption": caption,
                "byte_size": target.stat().st_size,
            }
            message = ChatMessage(
                thread_id=thread.thread_id,
                endpoint_id=endpoint.endpoint_id,
                direction="OUT",
                message_type=media_type,
                text=caption,
                media_json=_json(media),
                status="PENDING",
                reply_to_message_id=reply_to,
            )
            db.add(message)
            db.flush()
            meta = ChatMessageMeta(message_id=message.message_id, view_once=bool(view_once))
            db.add(meta)
            message_id = message.message_id
        self.events.publish({"type": "message", "thread_id": int(thread_id), "message_id": message_id})
        return self._dispatch_media(message_id)

    def retry(self, message_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None or message.direction != "OUT":
                raise KeyError(message_id)
            if message.status != "FAILED":
                raise ValueError("Nur fehlgeschlagene Nachrichten können erneut gesendet werden")
            message.status = "PENDING"
            message.error = None
            message.provider_message_id = None
            message_type = message.message_type
        self.events.publish({"type": "message_update", "message_id": int(message_id), "status": "PENDING"})
        if message_type == "TEXT":
            return self._dispatch_text(int(message_id))
        if message_type in MEDIA_TYPES:
            return self._dispatch_media(int(message_id))
        raise ValueError("Dieser Nachrichtentyp kann nicht erneut gesendet werden")

    def _ingest_message(self, payload: dict[str, Any]) -> None:
        provider_id = payload.get("provider_message_id")
        super()._ingest_message(payload)
        if not provider_id:
            return
        quote = payload.get("quote") if isinstance(payload.get("quote"), dict) else None
        with self.database.session() as db:
            message = db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == provider_id))
            if message is None:
                return
            meta = self._ensure_meta(db, message)
            if payload.get("provider_key"):
                meta.provider_key_json = _json(payload.get("provider_key"))
            meta.view_once = bool(payload.get("view_once"))
            if quote:
                meta.quote_provider_message_id = quote.get("provider_message_id")
                meta.quote_snapshot_json = _json(quote.get("snapshot"))
                if quote.get("provider_message_id"):
                    quoted = db.scalar(select(ChatMessage).where(
                        ChatMessage.provider_message_id == quote.get("provider_message_id")
                    ))
                    if quoted is not None and quoted.thread_id == message.thread_id:
                        message.reply_to_message_id = quoted.message_id
            meta.updated_at = utcnow()

    def _ingest_reaction(self, payload: dict[str, Any]) -> None:
        target_key = payload.get("target_key") or {}
        provider_id = target_key.get("id")
        if not provider_id:
            return
        browser_event = None
        with self.database.session() as db:
            message = db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == provider_id))
            if message is None:
                return
            actor = "ME" if payload.get("from_me") else "REMOTE"
            existing = db.scalar(select(ChatReaction).where(
                ChatReaction.message_id == message.message_id,
                ChatReaction.actor == actor,
            ))
            emoji = str(payload.get("emoji") or "")[:32]
            if not emoji:
                if existing is not None:
                    db.delete(existing)
            elif existing is None:
                db.add(ChatReaction(
                    message_id=message.message_id,
                    actor=actor,
                    emoji=emoji,
                    provider_key_json=_json(payload.get("reaction_key")),
                    created_at=_utc_from_ms(payload.get("timestamp_ms")),
                    updated_at=utcnow(),
                ))
            else:
                existing.emoji = emoji
                existing.provider_key_json = _json(payload.get("reaction_key"))
                existing.updated_at = utcnow()
            browser_event = {
                "type": "reaction_update",
                "thread_id": message.thread_id,
                "message_id": message.message_id,
            }
        if browser_event:
            self.events.publish(browser_event)

    def _update_played(self, payload: dict[str, Any]) -> None:
        provider_id = payload.get("provider_message_id")
        if not provider_id:
            return
        browser_event = None
        with self.database.session() as db:
            message = db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == provider_id))
            if message is not None and STATUS_RANK.get(message.status, 0) < STATUS_RANK["PLAYED"]:
                message.status = "PLAYED"
                browser_event = {
                    "type": "message_update",
                    "thread_id": message.thread_id,
                    "message_id": message.message_id,
                    "status": "PLAYED",
                }
        if browser_event:
            self.events.publish(browser_event)

    def _worker_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "reaction":
            try:
                self._ingest_reaction(event.get("payload") or {})
            except Exception:
                LOGGER.exception("Failed to ingest WhatsApp reaction")
            return
        if event_type == "message_update" and (event.get("payload") or {}).get("status") == "PLAYED":
            self._update_played(event.get("payload") or {})
            return
        super()._worker_event(event)
        if event_type == "connection" and str(event.get("state") or "").upper() == "CONNECTED":
            try:
                self.flush_pending_read_receipts()
            except Exception:
                LOGGER.debug("Pending read receipt flush failed", exc_info=True)

    def _read_keys_for_thread(self, db, thread_id: int, message_ids: set[int] | None = None):
        query = select(ChatMessage).where(
            ChatMessage.thread_id == int(thread_id),
            ChatMessage.direction == "IN",
        ).order_by(ChatMessage.timestamp, ChatMessage.message_id)
        rows = db.scalars(query).all()
        keys: list[tuple[ChatMessageMeta, dict[str, Any]]] = []
        now = utcnow()
        for message in rows:
            if message_ids is not None and message.message_id not in message_ids:
                continue
            meta = self._ensure_meta(db, message)
            if meta.read_by_me_at is None:
                meta.read_by_me_at = now
            key = _load(meta.provider_key_json, None)
            if key and meta.read_receipt_sent_at is None:
                keys.append((meta, key))
        return keys

    def mark_read(self, thread_id: int, message_ids: list[int] | None = None) -> dict[str, Any]:
        requested = {int(x) for x in message_ids or []} or None
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            keys = self._read_keys_for_thread(db, thread.thread_id, requested)
            thread.unread_count = 0
            thread.last_read_at = utcnow()
            key_values = [key for _, key in keys]
        sent = False
        if key_values and self.worker.state == "CONNECTED":
            try:
                self.worker.command("mark_read", keys=key_values, timeout=15)
                sent = True
            except Exception:
                LOGGER.debug("WhatsApp read receipt deferred thread_id=%s", thread_id, exc_info=True)
        if sent:
            now = utcnow()
            ids = {str(key.get("id")) for key in key_values if key.get("id")}
            with self.database.session() as db:
                rows = db.scalars(select(ChatMessage).where(
                    ChatMessage.thread_id == int(thread_id),
                    ChatMessage.direction == "IN",
                )).all()
                for message in rows:
                    meta = message.extra
                    key = _load(meta.provider_key_json, None) if meta else None
                    if meta and key and str(key.get("id")) in ids:
                        meta.read_receipt_sent_at = now
                        meta.updated_at = now
        self.events.publish({"type": "read_update", "thread_id": int(thread_id)})
        return {"thread_id": int(thread_id), "unread_count": 0, "provider_receipts": len(key_values) if sent else 0}

    def flush_pending_read_receipts(self) -> int:
        if self.worker.state != "CONNECTED":
            return 0
        pending: list[tuple[int, dict[str, Any]]] = []
        with self.database.session() as db:
            metas = db.scalars(select(ChatMessageMeta).where(
                ChatMessageMeta.read_by_me_at.is_not(None),
                ChatMessageMeta.read_receipt_sent_at.is_(None),
            )).all()
            for meta in metas:
                message = db.get(ChatMessage, meta.message_id)
                key = _load(meta.provider_key_json, None)
                if message is not None and message.direction == "IN" and key:
                    pending.append((meta.message_id, key))
        count = 0
        for offset in range(0, len(pending), 100):
            batch = pending[offset:offset + 100]
            self.worker.command("mark_read", keys=[key for _, key in batch], timeout=15)
            now = utcnow()
            with self.database.session() as db:
                for message_id, _ in batch:
                    meta = db.get(ChatMessageMeta, int(message_id))
                    if meta:
                        meta.read_receipt_sent_at = now
                        meta.updated_at = now
            count += len(batch)
        return count

    def send_reaction(self, message_id: int, emoji: str) -> dict[str, Any]:
        emoji = str(emoji or "")[:32]
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None:
                raise KeyError(message_id)
            endpoint = db.get(ContactEndpoint, message.endpoint_id) if message.endpoint_id else None
            target_key = self._provider_key(message)
            if not target_key:
                raise ValueError("Für diese Nachricht fehlt der WhatsApp-Key")
            jid = self._endpoint_jid(endpoint)
            thread_id = message.thread_id
        self.worker.command("send_reaction", jid=jid, target_key=target_key, emoji=emoji, timeout=30)
        with self.database.session() as db:
            existing = db.scalar(select(ChatReaction).where(
                ChatReaction.message_id == int(message_id), ChatReaction.actor == "ME"
            ))
            if not emoji:
                if existing:
                    db.delete(existing)
            elif existing:
                existing.emoji = emoji
                existing.updated_at = utcnow()
            else:
                db.add(ChatReaction(message_id=int(message_id), actor="ME", emoji=emoji))
        self.events.publish({"type": "reaction_update", "thread_id": thread_id, "message_id": int(message_id)})
        return self.message(int(message_id))

    def edit_message(self, message_id: int, text: str) -> dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            raise ValueError("Nachricht ist leer")
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None or message.direction != "OUT" or message.message_type != "TEXT":
                raise ValueError("Nur eigene Textnachrichten können bearbeitet werden")
            key = self._provider_key(message)
            endpoint = db.get(ContactEndpoint, message.endpoint_id) if message.endpoint_id else None
            if not key:
                raise ValueError("Für diese Nachricht fehlt der WhatsApp-Key")
            jid = self._endpoint_jid(endpoint)
            thread_id = message.thread_id
        self.worker.command("edit_message", jid=jid, target_key=key, text=text, timeout=30)
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            message.text = text
            meta = self._ensure_meta(db, message)
            meta.edited_at = utcnow()
            meta.updated_at = utcnow()
        self.events.publish({"type": "message_update", "thread_id": thread_id, "message_id": int(message_id)})
        return self.message(int(message_id))

    def delete_message(self, message_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None or message.direction != "OUT":
                raise ValueError("Nur eigene Nachrichten können für alle gelöscht werden")
            key = self._provider_key(message)
            endpoint = db.get(ContactEndpoint, message.endpoint_id) if message.endpoint_id else None
            if not key:
                raise ValueError("Für diese Nachricht fehlt der WhatsApp-Key")
            jid = self._endpoint_jid(endpoint)
            thread_id = message.thread_id
        self.worker.command("delete_message", jid=jid, target_key=key, timeout=30)
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            meta = self._ensure_meta(db, message)
            meta.deleted_at = utcnow()
            meta.updated_at = utcnow()
            message.text = None
            message.media_json = None
            message.status = "DELETED"
        self.events.publish({"type": "message_update", "thread_id": thread_id, "message_id": int(message_id), "status": "DELETED"})
        return self.message(int(message_id))

    def view_once_path(self, message_id: int) -> tuple[Path, str]:
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None:
                raise KeyError(message_id)
            meta = self._ensure_meta(db, message)
            if not meta.view_once:
                raise ValueError("Diese Nachricht ist kein Einmal-Medium")
            if message.direction != "IN":
                raise ValueError("Gesendete Einmal-Medien können nicht erneut geöffnet werden")
            if meta.view_once_consumed_at is not None:
                raise RuntimeError("Einmal-Medium wurde bereits geöffnet")
            media = _load(message.media_json, {}) or {}
            name = Path(str(media.get("archive_name") or "")).name
            target = (self.media_dir / name).resolve()
            if not name or not target.exists():
                raise FileNotFoundError(name)
            meta.view_once_consumed_at = utcnow()
            meta.updated_at = utcnow()
            mime = str(media.get("mime_type") or "application/octet-stream")
            return target, mime

    @staticmethod
    def _message_payload(message: ChatMessage | None) -> dict[str, Any] | None:
        payload = LegacyMessagingService._message_payload(message)
        if payload is None or message is None:
            return payload
        meta = message.extra
        payload["provider_key"] = _load(meta.provider_key_json, None) if meta else None
        payload["quote_snapshot"] = _load(meta.quote_snapshot_json, None) if meta else None
        payload["view_once"] = bool(meta.view_once) if meta else False
        payload["view_once_consumed"] = bool(meta and meta.view_once_consumed_at)
        payload["edited_at"] = meta.edited_at.isoformat() if meta and meta.edited_at else None
        payload["deleted_at"] = meta.deleted_at.isoformat() if meta and meta.deleted_at else None
        payload["read_by_me_at"] = meta.read_by_me_at.isoformat() if meta and meta.read_by_me_at else None
        payload["reactions"] = [
            {"actor": row.actor, "emoji": row.emoji, "updated_at": row.updated_at.isoformat()}
            for row in sorted(message.reactions or [], key=lambda item: item.actor)
        ]
        if payload["view_once"]:
            media = dict(payload.get("media") or {})
            media.pop("local_url", None)
            if payload["view_once_consumed"]:
                media = {"consumed": True, "mime_type": media.get("mime_type")}
            payload["media"] = media or None
        if payload["deleted_at"]:
            payload["text"] = None
            payload["media"] = None
        return payload
