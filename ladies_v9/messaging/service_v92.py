from __future__ import annotations

import base64
import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import and_, desc, func, or_, select, update

from ladies_v9.database import Database
from ladies_v9.models import AppSetting, Person, PersonIdentifier, utcnow
from .integration import install_identity_merge_listener
from .models import ChatMessage, ChatThread, ContactEndpoint
from .service import EventHub, MessagingService as BaseMessagingService, jid_phone
from .whatsapp_manager import BaileysWorkerManager

LOGGER = logging.getLogger("ladies_explorer.messaging")
TYPING_SETTING_KEY = "whatsapp_typing_enabled"
STATUS_RANK = {"PENDING": 0, "FAILED": 0, "SENT": 1, "DELIVERED": 2, "READ": 3, "RECEIVED": 1}


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class MessagingService(BaseMessagingService):
    """V9.2 lifecycle, Baileys-7 identity handling and browser-safe events."""

    def __init__(self, db_path: str | Path, data_dir: str | Path):
        install_identity_merge_listener()
        self.database = Database(db_path)
        from . import models as _messaging_models  # noqa: F401
        self.database.create_schema()
        self.events = EventHub()
        with self.database.session() as db:
            setting = db.get(AppSetting, TYPING_SETTING_KEY)
            self.typing_enabled = setting is None or setting.value_json.strip().lower() != "false"
        self.data_dir = Path(data_dir)
        self.auth_dir = self.data_dir / "whatsapp" / "auth"
        self.media_dir = self.data_dir / "whatsapp" / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        root = Path(__file__).resolve().parent
        self.worker = BaileysWorkerManager(root / "whatsapp_worker", self.auth_dir, self._worker_event)
        ok, detail = self.worker.dependency_status()
        if not ok:
            self.worker.state = "DEPENDENCY_MISSING"
            self.worker.detail = detail
        elif (self.auth_dir / "creds.json").exists():
            self.worker.start()
        else:
            self.worker.state = "NOT_CONFIGURED"

    def set_typing_enabled(self, value: bool) -> None:
        self.typing_enabled = bool(value)
        with self.database.session() as db:
            row = db.get(AppSetting, TYPING_SETTING_KEY)
            stored = "true" if self.typing_enabled else "false"
            if row is None:
                db.add(AppSetting(key=TYPING_SETTING_KEY, value_json=stored))
            else:
                row.value_json = stored
                row.updated_at = utcnow()

    def connect(self) -> dict[str, Any]:
        ok, detail = self.worker.dependency_status()
        if not ok:
            self.worker.state = "DEPENDENCY_MISSING"
            self.worker.detail = detail
            return self.status()
        try:
            if self.worker.proc and self.worker.proc.poll() is None:
                self.worker.command("connect", timeout=15)
            else:
                self.worker.start()
        except Exception as exc:
            LOGGER.warning("WhatsApp connect failed: %s", exc)
        return self.status()

    def logout(self) -> dict[str, Any]:
        try:
            if self.worker.proc and self.worker.proc.poll() is None:
                self.worker.command("logout", timeout=10)
        except Exception:
            LOGGER.exception("WhatsApp logout failed")
        try:
            shutil.rmtree(self.auth_dir, ignore_errors=True)
            self.auth_dir.mkdir(parents=True, exist_ok=True)
        finally:
            self.worker.qr = None
            self.worker.account = None
            self.worker.state = "LOGGED_OUT"
        return self.status()

    def sync_person(self, person_id: int) -> list[int]:
        created: list[int] = []
        with self.database.session() as db:
            if db.get(Person, int(person_id)) is None:
                raise KeyError(person_id)
            identifiers = db.scalars(
                select(PersonIdentifier)
                .where(PersonIdentifier.person_id == int(person_id), PersonIdentifier.kind == "PHONE")
                .order_by(PersonIdentifier.last_seen_at)
            ).all()
            for ident in identifiers:
                endpoint = db.scalar(select(ContactEndpoint).where(
                    ContactEndpoint.provider == "WHATSAPP",
                    ContactEndpoint.normalized_phone == ident.normalized_value,
                ))
                if endpoint is None:
                    endpoint = ContactEndpoint(
                        person_id=person_id,
                        normalized_phone=ident.normalized_value,
                        display_phone=ident.raw_value,
                        source_listing_id=ident.listing_id,
                        state="ACTIVE",
                    )
                    db.add(endpoint)
                    db.flush()
                    created.append(endpoint.endpoint_id)
                    thread = self._get_thread(db, person_id=person_id)
                    if db.scalar(select(func.count(ChatMessage.message_id)).where(ChatMessage.thread_id == thread.thread_id)):
                        db.add(ChatMessage(
                            thread_id=thread.thread_id,
                            endpoint_id=endpoint.endpoint_id,
                            direction="SYSTEM",
                            message_type="SYSTEM",
                            text=f"Neue Telefonnummer erkannt. {endpoint.display_phone or endpoint.normalized_phone} wurde dieser Person zugeordnet.",
                            status="RECEIVED",
                        ))
                elif endpoint.person_id is None:
                    self._assign_endpoint_in_session(db, endpoint, int(person_id))
                elif endpoint.person_id != int(person_id):
                    endpoint.state = "IDENTITY_CONFLICT"
                    LOGGER.warning(
                        "Endpoint identity conflict endpoint_id=%s existing_person=%s candidate_person=%s",
                        endpoint.endpoint_id,
                        endpoint.person_id,
                        person_id,
                    )
                if endpoint.last_seen_at and ident.last_seen_at:
                    endpoint.last_seen_at = max(_as_utc(endpoint.last_seen_at), _as_utc(ident.last_seen_at))
                elif ident.last_seen_at:
                    endpoint.last_seen_at = ident.last_seen_at
                endpoint.source_listing_id = ident.listing_id or endpoint.source_listing_id
                endpoint.updated_at = utcnow()
        return created

    def _archive_incoming_media(self, payload: dict[str, Any]) -> dict[str, Any]:
        media = payload.get("media")
        if not isinstance(media, dict) or not media.get("data_base64"):
            return payload
        try:
            raw = base64.b64decode(media["data_base64"], validate=True)
            digest = hashlib.sha256(raw).hexdigest()
            mime = str(media.get("mime_type") or "application/octet-stream").lower()
            ext = ".jpg" if "jpeg" in mime or "jpg" in mime else ".png" if "png" in mime else ".webp" if "webp" in mime else ".bin"
            path = self.media_dir / f"{digest}{ext}"
            if not path.exists():
                path.write_bytes(raw)
            clean_media = {k: v for k, v in media.items() if k != "data_base64"}
            clean_media["local_url"] = f"/archive/whatsapp/media/{path.name}"
            clean_media["byte_size"] = len(raw)
            payload = dict(payload)
            payload["media"] = clean_media
        except Exception:
            LOGGER.exception("Incoming WhatsApp media could not be archived")
        return payload

    @staticmethod
    def _phone_person_ids(db, phone: str | None) -> set[int]:
        if not phone:
            return set()
        return set(db.scalars(select(PersonIdentifier.person_id).where(
            PersonIdentifier.kind == "PHONE",
            PersonIdentifier.normalized_value == phone,
        )).all())

    @staticmethod
    def _ensure_phone_identifier(db, person_id: int, phone: str, raw_value: str | None = None) -> None:
        existing = db.scalar(select(PersonIdentifier).where(
            PersonIdentifier.person_id == int(person_id),
            PersonIdentifier.kind == "PHONE",
            PersonIdentifier.normalized_value == phone,
        ))
        if existing is None:
            db.add(PersonIdentifier(
                person_id=int(person_id),
                kind="PHONE",
                normalized_value=phone,
                raw_value=raw_value or phone,
            ))

    def _resolve_discovered_phone(
        self,
        db,
        endpoint: ContactEndpoint,
        phone: str | None,
        display_phone: str | None = None,
    ) -> ContactEndpoint:
        """Attach newly exposed PN evidence without silently crossing person identities."""
        if not phone:
            return endpoint
        phone_endpoint = db.scalar(select(ContactEndpoint).where(
            ContactEndpoint.provider == "WHATSAPP",
            ContactEndpoint.normalized_phone == phone,
            ContactEndpoint.endpoint_id != endpoint.endpoint_id,
        ).limit(1))
        people = self._phone_person_ids(db, phone)
        if endpoint.person_id is not None:
            people.add(int(endpoint.person_id))
        if phone_endpoint is not None and phone_endpoint.person_id is not None:
            people.add(int(phone_endpoint.person_id))
        if len(people) > 1:
            endpoint.state = "IDENTITY_CONFLICT"
            endpoint.updated_at = utcnow()
            LOGGER.warning(
                "Discovered phone conflicts with person identity endpoint_id=%s person_ids=%s",
                endpoint.endpoint_id,
                sorted(people),
            )
            return endpoint

        person_id = next(iter(people), None)
        if person_id is not None and endpoint.person_id is None:
            self._assign_endpoint_in_session(db, endpoint, person_id, add_system_message=False)
        if phone_endpoint is not None and person_id is not None and phone_endpoint.person_id is None:
            self._assign_endpoint_in_session(db, phone_endpoint, person_id, add_system_message=False)

        if phone_endpoint is not None:
            if endpoint.person_id is not None and phone_endpoint.person_id is not None and endpoint.person_id != phone_endpoint.person_id:
                endpoint.state = "IDENTITY_CONFLICT"
                return endpoint
            provider_address = endpoint.provider_address if endpoint.provider_address and (
                not phone_endpoint.provider_address or endpoint.provider_address.endswith("@lid")
            ) else None
            db.execute(update(ChatMessage).where(
                ChatMessage.endpoint_id == endpoint.endpoint_id
            ).values(endpoint_id=phone_endpoint.endpoint_id))
            # Delete/flush first so the unique provider_address constraint can never
            # see the old and canonical endpoint holding the same LID simultaneously.
            db.delete(endpoint)
            db.flush()
            endpoint = phone_endpoint
            if provider_address:
                endpoint.provider_address = provider_address
        else:
            endpoint.normalized_phone = phone

        endpoint.display_phone = display_phone or endpoint.display_phone or phone
        endpoint.state = "ACTIVE"
        endpoint.last_seen_at = utcnow()
        endpoint.updated_at = utcnow()
        if endpoint.person_id is not None:
            self._ensure_phone_identifier(db, int(endpoint.person_id), phone, endpoint.display_phone)
        return endpoint

    def _ingest_message(self, payload: dict[str, Any]) -> None:
        """Ingest Baileys 7 messages without ever treating a LID as a phone."""
        payload = self._archive_incoming_media(payload)
        provider_id = payload.get("provider_message_id")
        if not provider_id:
            return
        provider_address = str(payload.get("jid") or "") or None
        phone_jid = str(payload.get("phone_jid") or "") or None
        phone = jid_phone(phone_jid) if phone_jid else (
            jid_phone(provider_address)
            if provider_address and provider_address.endswith("@s.whatsapp.net")
            else None
        )

        with self.database.session() as db:
            if db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == provider_id)) is not None:
                return
            endpoint = None
            if provider_address:
                endpoint = db.scalar(select(ContactEndpoint).where(
                    ContactEndpoint.provider == "WHATSAPP",
                    ContactEndpoint.provider_address == provider_address,
                ))
            if endpoint is None and phone:
                endpoint = db.scalar(select(ContactEndpoint).where(
                    ContactEndpoint.provider == "WHATSAPP",
                    ContactEndpoint.normalized_phone == phone,
                ))
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
                        self._assign_endpoint_in_session(db, endpoint, int(next(iter(person_ids))), add_system_message=False)
            thread = self._get_thread(db, person_id=endpoint.person_id, endpoint=endpoint)
            provider_ts = payload.get("timestamp")
            timestamp = datetime.fromtimestamp(float(provider_ts), timezone.utc) if provider_ts else utcnow()
            message = ChatMessage(
                thread_id=thread.thread_id,
                endpoint_id=endpoint.endpoint_id,
                provider_message_id=provider_id,
                direction="OUT" if payload.get("from_me") else "IN",
                message_type=payload.get("message_type") or "TEXT",
                text=payload.get("text"),
                media_json=json.dumps(payload.get("media"), ensure_ascii=False) if payload.get("media") else None,
                timestamp=timestamp,
                provider_timestamp=timestamp,
                status="SENT" if payload.get("from_me") else "RECEIVED",
            )
            db.add(message)
            thread.last_message_at = timestamp
            thread.updated_at = utcnow()
            if not payload.get("from_me"):
                thread.unread_count += 1
            db.flush()
            browser_event = {"type": "message", "thread_id": thread.thread_id, "message_id": message.message_id}
        self.events.publish(browser_event)

    def _dispatch_text(self, message_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None or message.direction != "OUT":
                raise KeyError(message_id)
            endpoint = db.get(ContactEndpoint, message.endpoint_id) if message.endpoint_id else None
            thread = db.get(ChatThread, message.thread_id)
            if endpoint is None or thread is None or thread.person_id is None or endpoint.person_id != thread.person_id:
                raise ValueError("Kein gültiger WhatsApp-Endpunkt ausgewählt")
            if endpoint.state != "ACTIVE":
                raise ValueError("WhatsApp-Endpunkt hat einen ungeklärten Identity-Konflikt")
            if not endpoint.provider_address:
                if not endpoint.normalized_phone:
                    raise ValueError("Für diesen WhatsApp-Endpunkt ist keine sendefähige Adresse bekannt")
                endpoint.provider_address = endpoint.normalized_phone.lstrip("+") + "@s.whatsapp.net"
            jid = endpoint.provider_address
            text = message.text or ""

        try:
            response = self.worker.command(
                "send_text",
                jid=jid,
                text=text,
                client_message_id=str(message_id),
                timeout=30,
            )
            provider_message_id = (response.get("payload") or {}).get("provider_message_id")
            if not provider_message_id:
                raise RuntimeError("WhatsApp lieferte keine Nachrichten-ID")
            with self.database.session() as db:
                message = db.get(ChatMessage, int(message_id))
                if message is None:
                    raise KeyError(message_id)
                duplicate = db.scalar(select(ChatMessage).where(
                    ChatMessage.provider_message_id == provider_message_id,
                    ChatMessage.message_id != message.message_id,
                ))
                if duplicate is not None and duplicate.direction == "OUT" and duplicate.thread_id == message.thread_id:
                    if STATUS_RANK.get(duplicate.status, 0) > STATUS_RANK.get(message.status, 0):
                        message.status = duplicate.status
                    db.delete(duplicate)
                    db.flush()
                elif duplicate is not None:
                    raise RuntimeError("Provider-Nachrichten-ID ist bereits anderweitig belegt")
                message.provider_message_id = provider_message_id
                if STATUS_RANK.get(message.status, 0) < STATUS_RANK["SENT"]:
                    message.status = "SENT"
                message.error = None
                thread = db.get(ChatThread, message.thread_id)
                if thread:
                    thread.last_message_at = message.timestamp
                    thread.updated_at = utcnow()
        except Exception as exc:
            with self.database.session() as db:
                message = db.get(ChatMessage, int(message_id))
                if message:
                    message.status = "FAILED"
                    message.error = str(exc)[:1000]
            self.events.publish({"type": "message_update", "message_id": int(message_id), "status": "FAILED"})
            raise
        result = self.message(int(message_id))
        self.events.publish({
            "type": "message_update",
            "thread_id": result["thread_id"],
            "message_id": int(message_id),
            "status": result["status"],
        })
        return result

    def send_text(self, thread_id: int, text: str, endpoint_id: int | None = None, reply_to: int | None = None) -> dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            raise ValueError("Nachricht ist leer")
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            endpoint = db.get(ContactEndpoint, int(endpoint_id)) if endpoint_id else self._choose_endpoint(db, thread.person_id)
            if endpoint is None or thread.person_id is None or endpoint.person_id != thread.person_id:
                raise ValueError("Kein gültiger WhatsApp-Endpunkt ausgewählt")
            if endpoint.state != "ACTIVE":
                raise ValueError("WhatsApp-Endpunkt hat einen ungeklärten Identity-Konflikt")
            message = ChatMessage(
                thread_id=thread.thread_id,
                endpoint_id=endpoint.endpoint_id,
                direction="OUT",
                message_type="TEXT",
                text=text,
                status="PENDING",
                reply_to_message_id=reply_to,
            )
            db.add(message)
            db.flush()
            message_id = message.message_id
        self.events.publish({"type": "message", "thread_id": int(thread_id), "message_id": message_id})
        return self._dispatch_text(message_id)

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
        self.events.publish({"type": "message_update", "message_id": int(message_id), "status": "PENDING"})
        return self._dispatch_text(int(message_id))

    def thread(self, thread_id: int, limit: int = 100, before_message_id: int | None = None) -> dict[str, Any]:
        limit = max(1, min(200, int(limit)))
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            query = select(ChatMessage).where(ChatMessage.thread_id == thread.thread_id)
            if before_message_id is not None:
                cursor = db.get(ChatMessage, int(before_message_id))
                if cursor is None or cursor.thread_id != thread.thread_id:
                    raise KeyError(before_message_id)
                query = query.where(or_(
                    ChatMessage.timestamp < cursor.timestamp,
                    and_(ChatMessage.timestamp == cursor.timestamp, ChatMessage.message_id < cursor.message_id),
                ))
            rows = db.scalars(
                query.order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id)).limit(limit + 1)
            ).all()
            has_more = len(rows) > limit
            rows = rows[:limit]
            rows.reverse()
            endpoints = db.scalars(
                select(ContactEndpoint)
                .where(ContactEndpoint.person_id == thread.person_id)
                .order_by(desc(ContactEndpoint.last_seen_at))
            ).all() if thread.person_id else []
            latest = db.scalar(select(ChatMessage).where(
                ChatMessage.thread_id == thread.thread_id
            ).order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id)).limit(1))
            payload = self._thread_payload(db, thread, latest)
            payload["messages"] = [self._message_payload(x) for x in rows]
            payload["endpoints"] = [self._endpoint_payload(x) for x in endpoints]
            payload["has_more"] = has_more
            payload["next_before"] = rows[0].message_id if has_more and rows else None
        if before_message_id is None:
            self.subscribe_presence(thread_id)
        return payload

    def _presence_browser_event(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        state = payload.get("state")
        if state not in {"composing", "paused", "recording", "available", "unavailable"}:
            return None
        addresses = [str(x) for x in (payload.get("jid"), payload.get("participant")) if x]
        phones = {jid_phone(x) for x in addresses if x.endswith("@s.whatsapp.net")}
        phones.discard(None)
        with self.database.session() as db:
            endpoint = None
            if addresses:
                endpoint = db.scalar(select(ContactEndpoint).where(
                    ContactEndpoint.provider == "WHATSAPP",
                    ContactEndpoint.provider_address.in_(addresses),
                ).limit(1))
            if endpoint is None and phones:
                endpoint = db.scalar(select(ContactEndpoint).where(
                    ContactEndpoint.provider == "WHATSAPP",
                    ContactEndpoint.normalized_phone.in_(phones),
                ).limit(1))
            if endpoint is None:
                return None
            if endpoint.person_id is not None:
                thread = db.scalar(select(ChatThread).where(ChatThread.person_id == endpoint.person_id))
            else:
                thread = db.scalar(
                    select(ChatThread)
                    .join(ChatMessage, ChatMessage.thread_id == ChatThread.thread_id)
                    .where(ChatThread.person_id.is_(None), ChatMessage.endpoint_id == endpoint.endpoint_id)
                    .limit(1)
                )
            if thread is None:
                return None
            return {
                "type": "presence",
                "thread_id": thread.thread_id,
                "endpoint_id": endpoint.endpoint_id,
                "person_id": endpoint.person_id,
                "state": state,
            }

    def _worker_event(self, event: dict[str, Any]) -> None:
        """Translate private worker IPC into minimal browser-safe domain events."""
        event_type = event.get("type")
        if event_type == "message":
            try:
                self._ingest_message(event.get("payload") or {})
            except Exception:
                LOGGER.exception("Failed to ingest WhatsApp message")
            return
        if event_type == "message_update":
            payload = event.get("payload") or {}
            provider_id = payload.get("provider_message_id")
            client_message_id = payload.get("client_message_id")
            status = payload.get("status")
            if not provider_id or status not in {"SENT", "DELIVERED", "READ", "FAILED"}:
                return
            browser_event = None
            with self.database.session() as db:
                message = db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == provider_id))
                if message is None and client_message_id:
                    try:
                        local = db.get(ChatMessage, int(client_message_id))
                    except (TypeError, ValueError):
                        local = None
                    if local is not None and local.direction == "OUT" and local.provider_message_id in {None, provider_id}:
                        conflict = db.scalar(select(ChatMessage).where(
                            ChatMessage.provider_message_id == provider_id,
                            ChatMessage.message_id != local.message_id,
                        ))
                        if conflict is None:
                            local.provider_message_id = provider_id
                            message = local
                if message is not None:
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
            return
        if event_type == "presence":
            browser_event = self._presence_browser_event(event.get("payload") or {})
            if browser_event:
                self.events.publish(browser_event)
            return
        if event_type == "connection":
            state = str(event.get("state") or "ERROR").upper()
            detail = event.get("detail")
            self.events.publish({"type": "connection", "state": state, "detail": detail})
            return
        if event_type == "qr":
            self.events.publish({"type": "qr_available"})
            return
        if event_type == "error":
            self.events.publish({"type": "worker_error", "code": str(event.get("code") or "WORKER_ERROR")[:80]})
            return
        # response/ready and any future raw transport event remain private to IPC.

    def _assign_endpoint_in_session(self, db, endpoint: ContactEndpoint, person_id: int, *, add_system_message: bool = True) -> None:
        person_id = int(person_id)
        if endpoint.normalized_phone:
            other_people = self._phone_person_ids(db, endpoint.normalized_phone) - {person_id}
            if other_people:
                endpoint.state = "IDENTITY_CONFLICT"
                raise ValueError("Telefonnummer ist bereits einer anderen Person zugeordnet")

        target = self._get_thread(db, person_id=person_id)
        temporary = db.scalar(
            select(ChatThread)
            .join(ChatMessage, ChatMessage.thread_id == ChatThread.thread_id)
            .where(ChatThread.person_id.is_(None), ChatMessage.endpoint_id == endpoint.endpoint_id)
            .limit(1)
        )
        if temporary and temporary.thread_id != target.thread_id:
            for message in db.scalars(select(ChatMessage).where(ChatMessage.thread_id == temporary.thread_id)).all():
                message.thread_id = target.thread_id
            target.unread_count += temporary.unread_count
            times = [x for x in (target.last_message_at, temporary.last_message_at) if x]
            if times:
                target.last_message_at = max(times)
            db.delete(temporary)
        endpoint.person_id = person_id
        endpoint.state = "ACTIVE"
        endpoint.updated_at = utcnow()
        if endpoint.normalized_phone:
            self._ensure_phone_identifier(db, person_id, endpoint.normalized_phone, endpoint.display_phone)
        if add_system_message:
            label = endpoint.display_phone or endpoint.normalized_phone or "WhatsApp-Endpunkt"
            db.add(ChatMessage(
                thread_id=target.thread_id,
                endpoint_id=endpoint.endpoint_id,
                direction="SYSTEM",
                message_type="SYSTEM",
                text=f"Nummer {label} dieser Person zugeordnet.",
                status="RECEIVED",
            ))

    def assignment_candidates(self, endpoint_id: int) -> list[dict[str, Any]]:
        with self.database.session() as db:
            endpoint = db.get(ContactEndpoint, int(endpoint_id))
            if endpoint is None:
                raise KeyError(endpoint_id)
            exact_ids = self._phone_person_ids(db, endpoint.normalized_phone)
            persons = db.scalars(select(Person).order_by(desc(Person.last_seen_at)).limit(50)).all()
            out: list[dict[str, Any]] = []
            for person in persons:
                exact = person.person_id in exact_ids
                evidence = [{
                    "kind": "PHONE" if exact else "MANUAL",
                    "decision": "MATCH" if exact else "NO_EVIDENCE",
                    "text": "Gleiche normalisierte Telefonnummer" if exact else "Keine automatische Evidence für diesen WhatsApp-Kontakt – manuelle Auswahl",
                }]
                out.append({
                    "person_id": person.person_id,
                    "display_name": person.display_name,
                    "evidence": evidence,
                    "exact_phone_match": exact,
                })
            out.sort(key=lambda x: (not x["exact_phone_match"], (x["display_name"] or "").lower()))
            return out
