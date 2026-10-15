from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, or_, select

from ladies_v9.models import utcnow

from .models import ChatMessage, ChatReaction, ChatThread, ContactEndpoint, ProviderPeerState
from .provider_identity import message_identity, message_raw_id, normalize_provider_key
from .service import (
    MEDIA_TYPES,
    STATUS_RANK,
    MessagingService as _BaseMessagingService,
    _json,
    _load,
    _utc_from_ms,
    jid_phone,
)

LOGGER = logging.getLogger("ladies_explorer.messaging.service_v2")


class MessagingService(_BaseMessagingService):
    """Canonical conversation service independent from discovery/profile state."""

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

    @staticmethod
    def _canonical_provider_id(payload: dict[str, Any]) -> str | None:
        canonical = message_identity(payload.get("provider_key"))
        if canonical:
            return canonical
        value = payload.get("provider_message_id")
        return str(value) if value else None

    @staticmethod
    def _direct_phone(provider_address: str | None, phone_address: str | None = None) -> str | None:
        for value in (phone_address, provider_address):
            if value and str(value).endswith("@s.whatsapp.net"):
                return jid_phone(str(value))
        return None

    def _conversation_endpoint(self, db, thread: ChatThread) -> ContactEndpoint | None:
        if thread.person_id is not None:
            return self._choose_endpoint(db, thread.person_id)
        return db.scalar(
            select(ContactEndpoint)
            .join(ChatMessage, ChatMessage.endpoint_id == ContactEndpoint.endpoint_id)
            .where(
                ChatMessage.thread_id == thread.thread_id,
                ChatMessage.endpoint_id.is_not(None),
                ContactEndpoint.person_id.is_(None),
                ContactEndpoint.state == "ACTIVE",
            )
            .order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id))
            .limit(1)
        )

    def _provider_peer_for_endpoint(self, db, endpoint: ContactEndpoint | None) -> ProviderPeerState | None:
        if endpoint is None:
            return None
        phone_jid = (
            endpoint.normalized_phone.lstrip("+") + "@s.whatsapp.net"
            if endpoint.normalized_phone
            else None
        )
        clauses = []
        if endpoint.provider_address:
            clauses.append(ProviderPeerState.provider_address == endpoint.provider_address)
        if phone_jid:
            clauses.extend(
                (
                    ProviderPeerState.provider_address == phone_jid,
                    ProviderPeerState.phone_address == phone_jid,
                )
            )
        if not clauses:
            return None
        return db.scalar(
            select(ProviderPeerState)
            .where(ProviderPeerState.provider == "WHATSAPP", or_(*clauses))
            .order_by(desc(ProviderPeerState.updated_at))
            .limit(1)
        )

    def _thread_payload(self, db, thread: ChatThread, last: ChatMessage | None) -> dict[str, Any]:
        payload = super()._thread_payload(db, thread, last)
        endpoint = self._conversation_endpoint(db, thread)
        payload["endpoint"] = self._endpoint_payload(endpoint) if endpoint else None
        if thread.person_id is None:
            peer = self._provider_peer_for_endpoint(db, endpoint)
            payload["display_name"] = (
                (peer.display_name if peer and peer.display_name else None)
                or (endpoint.display_phone if endpoint else None)
                or (endpoint.normalized_phone if endpoint else None)
                or "Unbekannte WhatsApp-Nummer"
            )
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
                if last is not None:
                    items.append(self._thread_payload(db, thread, last))
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
            endpoints.sort(key=lambda item: item.last_seen_at or item.created_at, reverse=True)
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

    def _save_send_result(self, message_id: int, response: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(response or {})
        payload = dict(normalized.get("payload") or {})
        canonical = message_identity(payload.get("provider_key"))
        if canonical:
            payload["provider_message_id"] = canonical
        normalized["payload"] = payload
        return super()._save_send_result(message_id, normalized)

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

    @staticmethod
    def _payload_phone(payload: dict[str, Any]) -> str | None:
        phone_jid = str(payload.get("phone_jid") or "") or None
        provider_address = str(payload.get("jid") or "") or None
        if phone_jid:
            return jid_phone(phone_jid)
        if provider_address and provider_address.endswith("@s.whatsapp.net"):
            return jid_phone(provider_address)
        return None

    def _endpoint_for_payload(self, db, payload: dict[str, Any], cache: dict[tuple[str, str], ContactEndpoint]) -> ContactEndpoint:
        provider_address = str(payload.get("jid") or "") or None
        phone = self._payload_phone(payload)
        for key in (("address", provider_address or ""), ("phone", phone or "")):
            if key[1] and key in cache:
                return cache[key]

        endpoint = None
        if provider_address:
            endpoint = db.scalar(
                select(ContactEndpoint).where(
                    ContactEndpoint.provider == "WHATSAPP",
                    ContactEndpoint.provider_address == provider_address,
                )
            )
        if endpoint is None and phone:
            endpoint = db.scalar(
                select(ContactEndpoint).where(
                    ContactEndpoint.provider == "WHATSAPP",
                    ContactEndpoint.normalized_phone == phone,
                )
            )
        if endpoint is None:
            person_ids = self._phone_person_ids(db, phone)
            person_id = next(iter(person_ids)) if len(person_ids) == 1 else None
            endpoint = ContactEndpoint(
                person_id=person_id,
                normalized_phone=phone,
                display_phone=payload.get("display_phone") or (phone if phone else "Nummer nicht verfügbar"),
                provider_address=provider_address,
                state="ACTIVE",
            )
            db.add(endpoint)
            db.flush()
            if person_id is not None and phone:
                self._ensure_phone_identifier(db, int(person_id), phone, endpoint.display_phone)
        else:
            if phone:
                endpoint = self._resolve_discovered_phone(db, endpoint, phone, payload.get("display_phone"))
            if provider_address and endpoint.state != "IDENTITY_CONFLICT":
                endpoint.provider_address = provider_address
            endpoint.last_seen_at = utcnow()
            endpoint.updated_at = utcnow()
            if endpoint.person_id is None and phone and endpoint.state != "IDENTITY_CONFLICT":
                person_ids = self._phone_person_ids(db, phone)
                if len(person_ids) == 1:
                    self._assign_endpoint_in_session(
                        db,
                        endpoint,
                        int(next(iter(person_ids))),
                        add_system_message=False,
                    )

        if provider_address:
            cache[("address", provider_address)] = endpoint
        if phone:
            cache[("phone", phone)] = endpoint
        return endpoint

    def _legacy_message_for_payload(
        self,
        db,
        payload: dict[str, Any],
        canonical_id: str,
        endpoint: ContactEndpoint,
    ) -> ChatMessage | None:
        key = normalize_provider_key(payload.get("provider_key"))
        raw_id = message_raw_id(key) or message_raw_id(payload.get("provider_message_id"))
        if not raw_id or raw_id == canonical_id:
            return None
        legacy = db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == raw_id))
        if legacy is None:
            return None
        expected_direction = "OUT" if payload.get("from_me") else "IN"
        if legacy.direction != expected_direction:
            return None
        meta_key = _load(legacy.extra.provider_key_json, None) if legacy.extra else None
        if meta_key and message_identity(meta_key) not in {None, canonical_id}:
            return None
        if legacy.endpoint_id:
            legacy_endpoint = db.get(ContactEndpoint, legacy.endpoint_id)
            addresses = {
                str(value)
                for value in (
                    key.get("remoteJid") if key else None,
                    key.get("remoteJidAlt") if key else None,
                )
                if value
            }
            if legacy_endpoint and addresses:
                endpoint_addresses = {
                    value
                    for value in (
                        legacy_endpoint.provider_address,
                        legacy_endpoint.normalized_phone.lstrip("+") + "@s.whatsapp.net"
                        if legacy_endpoint.normalized_phone
                        else None,
                    )
                    if value
                }
                if endpoint_addresses and not endpoint_addresses.intersection(addresses):
                    return None
        legacy.provider_message_id = canonical_id
        meta = self._ensure_meta(db, legacy)
        if key:
            meta.provider_key_json = _json(key)
        meta.updated_at = utcnow()
        return legacy

    @staticmethod
    def _thread_cache_key(endpoint: ContactEndpoint) -> tuple[str, int]:
        if endpoint.person_id is not None:
            return ("person", int(endpoint.person_id))
        return ("endpoint", int(endpoint.endpoint_id))

    def _raw_message_index(self, db, thread_id: int, cache: dict[int, dict[str, ChatMessage | None]]) -> dict[str, ChatMessage | None]:
        if thread_id in cache:
            return cache[thread_id]
        index: dict[str, ChatMessage | None] = {}
        rows = db.scalars(
            select(ChatMessage).where(
                ChatMessage.thread_id == int(thread_id),
                ChatMessage.provider_message_id.is_not(None),
            )
        ).all()
        for row in rows:
            raw = message_raw_id(row.provider_message_id)
            if not raw:
                continue
            index[raw] = row if raw not in index else None
        cache[thread_id] = index
        return index

    def _ingest_provider_batch(self, payloads: list[dict[str, Any]], *, rehydrated: bool) -> int:
        prepared: list[dict[str, Any]] = []
        if not rehydrated:
            self.history_cache.store(payloads)
        for source in payloads:
            if not isinstance(source, dict):
                continue
            payload = dict(source)
            canonical_id = self._canonical_provider_id(payload)
            if not canonical_id:
                continue
            payload["provider_message_id"] = canonical_id
            payload["rehydrated"] = bool(rehydrated or payload.get("rehydrated"))
            prepared.append(self.media_store.archive_incoming(payload))
        if not prepared:
            return 0

        canonical_ids = {str(item["provider_message_id"]) for item in prepared}
        inserted = 0
        with self.database.session() as db:
            existing_ids = set(
                db.scalars(
                    select(ChatMessage.provider_message_id).where(
                        ChatMessage.provider_message_id.in_(canonical_ids)
                    )
                ).all()
            )
            endpoint_cache: dict[tuple[str, str], ContactEndpoint] = {}
            thread_cache: dict[tuple[str, int], ChatThread] = {}
            raw_indexes: dict[int, dict[str, ChatMessage | None]] = {}

            for payload in prepared:
                canonical_id = str(payload["provider_message_id"])
                if canonical_id in existing_ids:
                    continue
                endpoint = self._endpoint_for_payload(db, payload, endpoint_cache)
                legacy = self._legacy_message_for_payload(db, payload, canonical_id, endpoint)
                if legacy is not None:
                    existing_ids.add(canonical_id)
                    continue

                thread_key = self._thread_cache_key(endpoint)
                thread = thread_cache.get(thread_key)
                if thread is None:
                    thread = self._get_thread(db, person_id=endpoint.person_id, endpoint=endpoint)
                    thread_cache[thread_key] = thread

                provider_ts = payload.get("timestamp")
                try:
                    timestamp = datetime.fromtimestamp(float(provider_ts), timezone.utc) if provider_ts else utcnow()
                except (TypeError, ValueError, OSError):
                    timestamp = utcnow()
                message = ChatMessage(
                    thread_id=thread.thread_id,
                    endpoint_id=endpoint.endpoint_id,
                    provider_message_id=canonical_id,
                    direction="OUT" if payload.get("from_me") else "IN",
                    message_type=payload.get("message_type") or "TEXT",
                    text=payload.get("text"),
                    media_json=_json(payload.get("media")),
                    timestamp=timestamp,
                    provider_timestamp=timestamp,
                    status="SENT" if payload.get("from_me") else "RECEIVED",
                )
                db.add(message)
                db.flush()
                meta = self._ensure_meta(db, message)
                provider_key = normalize_provider_key(payload.get("provider_key"))
                if provider_key:
                    meta.provider_key_json = _json(provider_key)
                meta.view_once = bool(payload.get("view_once"))

                raw_index = self._raw_message_index(db, thread.thread_id, raw_indexes)
                raw_id = message_raw_id(canonical_id)
                if raw_id:
                    raw_index[raw_id] = message if raw_id not in raw_index else None

                quote = payload.get("quote") if isinstance(payload.get("quote"), dict) else None
                if quote:
                    quote_raw = message_raw_id(quote.get("provider_message_id"))
                    meta.quote_provider_message_id = quote_raw
                    meta.quote_snapshot_json = _json(quote.get("snapshot"))
                    if quote_raw:
                        quoted = raw_index.get(quote_raw)
                        if quoted is not None and quoted.thread_id == message.thread_id:
                            message.reply_to_message_id = quoted.message_id
                meta.updated_at = utcnow()

                if thread.last_message_at is None or timestamp > thread.last_message_at:
                    thread.last_message_at = timestamp
                thread.updated_at = utcnow()
                if message.direction == "IN" and not payload.get("rehydrated"):
                    thread.unread_count += 1
                existing_ids.add(canonical_id)
                inserted += 1
        return inserted

    def _ingest_message(self, payload: dict[str, Any]) -> None:
        inserted = self._ingest_provider_batch([payload], rehydrated=bool(payload.get("rehydrated")))
        if not inserted:
            return
        canonical_id = self._canonical_provider_id(payload)
        if not canonical_id:
            return
        with self.database.session() as db:
            message = db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == canonical_id))
            if message is None:
                return
            browser_event = {
                "type": "message",
                "thread_id": message.thread_id,
                "message_id": message.message_id,
            }
        self.events.publish(browser_event)

    def _find_provider_message(self, db, payload: dict[str, Any]) -> ChatMessage | None:
        provider_key = payload.get("provider_key") or payload.get("target_key")
        canonical = message_identity(provider_key)
        provider_id = payload.get("provider_message_id")
        if canonical:
            message = db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == canonical))
            if message is not None:
                return message
        if provider_id:
            message = db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == str(provider_id)))
            if message is not None:
                return message
        raw_id = message_raw_id(provider_key) or message_raw_id(provider_id)
        if not raw_id:
            return None
        candidates = db.scalars(
            select(ChatMessage).where(ChatMessage.provider_message_id.is_not(None))
        ).all()
        matches = [item for item in candidates if message_raw_id(item.provider_message_id) == raw_id]
        return matches[0] if len(matches) == 1 else None

    def _ingest_reaction(self, payload: dict[str, Any]) -> None:
        browser_event = None
        with self.database.session() as db:
            message = self._find_provider_message(db, payload)
            if message is None:
                return
            actor = "ME" if payload.get("from_me") else "REMOTE"
            existing = db.scalar(
                select(ChatReaction).where(
                    ChatReaction.message_id == message.message_id,
                    ChatReaction.actor == actor,
                )
            )
            emoji = str(payload.get("emoji") or "")[:32]
            if not emoji:
                if existing is not None:
                    db.delete(existing)
            elif existing is None:
                db.add(
                    ChatReaction(
                        message_id=message.message_id,
                        actor=actor,
                        emoji=emoji,
                        provider_key_json=_json(payload.get("reaction_key")),
                        created_at=_utc_from_ms(payload.get("timestamp_ms")),
                        updated_at=utcnow(),
                    )
                )
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

    def _ingest_content_update(self, payload: dict[str, Any]) -> None:
        action = str(payload.get("action") or "").upper()
        if action not in {"EDIT", "DELETE"}:
            return
        browser_event = None
        with self.database.session() as db:
            message = self._find_provider_message(db, payload)
            if message is None:
                return
            meta = self._ensure_meta(db, message)
            now = utcnow()
            if action == "DELETE":
                media = _load(message.media_json, {}) or {}
                self.media_store.delete(media, private=bool(meta.view_once))
                message.text = None
                message.media_json = None
                message.status = "DELETED"
                meta.deleted_at = now
            else:
                message.text = payload.get("text")
                meta.edited_at = now
            if payload.get("provider_key"):
                meta.provider_key_json = _json(normalize_provider_key(payload.get("provider_key")))
            meta.updated_at = now
            browser_event = {
                "type": "message_update",
                "thread_id": message.thread_id,
                "message_id": message.message_id,
                "status": message.status,
            }
        if browser_event:
            self.events.publish(browser_event)

    def _ingest_status_update(self, payload: dict[str, Any]) -> None:
        status = str(payload.get("status") or "").upper()
        if status not in {"SENT", "DELIVERED", "READ", "PLAYED", "FAILED"}:
            return
        browser_event = None
        with self.database.session() as db:
            message = self._find_provider_message(db, payload)
            client_message_id = payload.get("client_message_id")
            if message is None and client_message_id:
                try:
                    message = db.get(ChatMessage, int(client_message_id))
                except (TypeError, ValueError):
                    message = None
            if message is None:
                return
            canonical = message_identity(payload.get("provider_key"))
            if canonical and message.provider_message_id in {None, payload.get("provider_message_id"), message_raw_id(canonical)}:
                conflict = db.scalar(
                    select(ChatMessage).where(
                        ChatMessage.provider_message_id == canonical,
                        ChatMessage.message_id != message.message_id,
                    )
                )
                if conflict is None:
                    message.provider_message_id = canonical
                    meta = self._ensure_meta(db, message)
                    meta.provider_key_json = _json(normalize_provider_key(payload.get("provider_key")))
                    meta.updated_at = utcnow()
            if status == "FAILED" or STATUS_RANK.get(status, 0) >= STATUS_RANK.get(message.status, 0):
                message.status = status
            browser_event = {
                "type": "message_update",
                "thread_id": message.thread_id,
                "message_id": message.message_id,
                "status": message.status,
            }
        if browser_event:
            self.events.publish(browser_event)

    @staticmethod
    def _peer_address(item: dict[str, Any]) -> str | None:
        value = item.get("provider_address") or item.get("id") or item.get("jid")
        return str(value) if value else None

    def _endpoint_for_peer(self, db, item: dict[str, Any]) -> ContactEndpoint | None:
        address = self._peer_address(item)
        phone_address = str(item.get("phone_jid") or item.get("phone_address") or "") or None
        endpoint = None
        if address:
            endpoint = db.scalar(
                select(ContactEndpoint).where(
                    ContactEndpoint.provider == "WHATSAPP",
                    ContactEndpoint.provider_address == address,
                )
            )
        phone = self._direct_phone(address, phone_address)
        if endpoint is None and phone:
            endpoint = db.scalar(
                select(ContactEndpoint).where(
                    ContactEndpoint.provider == "WHATSAPP",
                    ContactEndpoint.normalized_phone == phone,
                )
            )
        return endpoint

    @staticmethod
    def _existing_thread_for_endpoint(db, endpoint: ContactEndpoint) -> ChatThread | None:
        if endpoint.person_id is not None:
            return db.scalar(select(ChatThread).where(ChatThread.person_id == endpoint.person_id))
        return db.scalar(
            select(ChatThread)
            .join(ChatMessage, ChatMessage.thread_id == ChatThread.thread_id)
            .where(
                ChatThread.person_id.is_(None),
                ChatMessage.endpoint_id == endpoint.endpoint_id,
            )
            .limit(1)
        )

    def _apply_provider_metadata(
        self,
        contacts: list[dict[str, Any]] | None = None,
        chats: list[dict[str, Any]] | None = None,
        *,
        history: bool = False,
        deleted_addresses: list[str] | None = None,
    ) -> int:
        changed = 0
        contacts = [item for item in contacts or [] if isinstance(item, dict)]
        chats = [item for item in chats or [] if isinstance(item, dict)]
        deleted = {str(value) for value in deleted_addresses or [] if value}
        with self.database.session() as db:
            for item, kind in [(item, "CONTACT") for item in contacts] + [(item, "CHAT") for item in chats]:
                address = self._peer_address(item)
                if not address:
                    continue
                row = db.get(
                    ProviderPeerState,
                    {"provider": "WHATSAPP", "provider_address": address},
                )
                if row is None:
                    row = ProviderPeerState(provider="WHATSAPP", provider_address=address)
                    db.add(row)
                phone_address = item.get("phone_jid") or item.get("phone_address")
                if not phone_address and address.endswith("@s.whatsapp.net"):
                    phone_address = address
                if phone_address:
                    row.phone_address = str(phone_address)
                display_name = item.get("display_name") or item.get("name")
                if display_name:
                    row.display_name = str(display_name)[:256]
                if item.get("unread_count") is not None:
                    try:
                        row.unread_count = max(0, int(item.get("unread_count")))
                    except (TypeError, ValueError):
                        pass
                if kind == "CHAT":
                    row.chat_deleted = False
                row.state_json = _json(item)
                row.updated_at = utcnow()
                changed += 1

                if history and row.unread_count is not None:
                    endpoint = self._endpoint_for_peer(db, item)
                    if endpoint is not None:
                        thread = self._existing_thread_for_endpoint(db, endpoint)
                        if thread is not None:
                            thread.unread_count = row.unread_count
                            thread.updated_at = utcnow()

            for address in deleted:
                row = db.get(
                    ProviderPeerState,
                    {"provider": "WHATSAPP", "provider_address": address},
                )
                if row is None:
                    row = ProviderPeerState(provider="WHATSAPP", provider_address=address)
                    db.add(row)
                row.chat_deleted = True
                row.updated_at = utcnow()
                changed += 1
        return changed

    def _worker_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "history_batch":
            payloads = [item for item in event.get("payloads") or [] if isinstance(item, dict)]
            try:
                ingested = self._ingest_provider_batch(payloads, rehydrated=True)
            except Exception:
                LOGGER.exception("Failed to ingest WhatsApp history batch")
                return
            if ingested:
                self.events.publish({"type": "history_batch", "count": ingested})
            return
        if event_type == "history_metadata":
            try:
                changed = self._apply_provider_metadata(
                    event.get("contacts") or [],
                    event.get("chats") or [],
                    history=True,
                )
            except Exception:
                LOGGER.exception("Failed to ingest WhatsApp history metadata")
                return
            if changed:
                self.events.publish({"type": "history_metadata", "count": changed})
            return
        if event_type == "provider_contacts":
            self._apply_provider_metadata(contacts=event.get("items") or [])
            self.events.publish({"type": "provider_metadata"})
            return
        if event_type == "provider_chats":
            self._apply_provider_metadata(chats=event.get("items") or [])
            self.events.publish({"type": "provider_metadata"})
            return
        if event_type == "provider_chat_deleted":
            self._apply_provider_metadata(deleted_addresses=event.get("addresses") or [])
            self.events.publish({"type": "provider_metadata"})
            return
        if event_type == "reaction":
            try:
                self._ingest_reaction(event.get("payload") or {})
            except Exception:
                LOGGER.exception("Failed to ingest WhatsApp reaction")
            return
        if event_type == "message_content_update":
            try:
                self._ingest_content_update(event.get("payload") or {})
            except Exception:
                LOGGER.exception("Failed to ingest WhatsApp edit/delete update")
            return
        if event_type == "message_update":
            try:
                self._ingest_status_update(event.get("payload") or {})
            except Exception:
                LOGGER.exception("Failed to ingest WhatsApp status update")
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
