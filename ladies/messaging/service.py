from __future__ import annotations

import json
import logging
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import desc, func, select

from ladies_v9.database import Database
from ladies_v9.identity import normalize_phone_number
from ladies_v9.models import IdentityEvent, Listing, ListingSnapshot, MediaAsset, Person, PersonIdentifier, SnapshotMedia, utcnow
from .models import ChatMessage, ChatThread, ContactEndpoint
from .whatsapp_manager import BaileysWorkerManager

LOGGER = logging.getLogger("ladies_explorer.messaging")


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def jid_phone(jid: str | None) -> str | None:
    if not jid:
        return None
    local = str(jid).split("@", 1)[0].split(":", 1)[0]
    return normalize_phone_number(local)


class EventHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass


class MessagingService:
    def __init__(self, db_path: str | Path, data_dir: str | Path):
        self.database = Database(db_path)
        # Import registers the messaging tables in the shared SQLAlchemy metadata.
        from . import models as _messaging_models  # noqa: F401
        self.database.create_schema()
        self.events = EventHub()
        self.typing_enabled = True
        root = Path(__file__).resolve().parent
        self.worker = BaileysWorkerManager(
            root / "whatsapp_worker",
            Path(data_dir) / "whatsapp" / "auth",
            self._worker_event,
        )
        self.worker.start()

    def status(self) -> dict[str, Any]:
        out = dict(self.worker.status())
        out["typing_enabled"] = self.typing_enabled
        return out

    def connect(self) -> dict[str, Any]:
        self.worker.start()
        return self.status()

    def disconnect(self) -> dict[str, Any]:
        try:
            self.worker.command("disconnect")
        except Exception:
            LOGGER.exception("WhatsApp disconnect failed")
        return self.status()

    def logout(self) -> dict[str, Any]:
        try:
            self.worker.command("logout")
        except Exception:
            LOGGER.exception("WhatsApp logout failed")
        return self.status()

    def qr(self) -> str | None:
        return self.worker.qr

    def set_typing_enabled(self, value: bool) -> None:
        self.typing_enabled = bool(value)

    def _get_thread(self, db, person_id: int | None = None, endpoint: ContactEndpoint | None = None) -> ChatThread:
        if person_id is not None:
            row = db.scalar(select(ChatThread).where(ChatThread.person_id == person_id))
            if row is not None:
                return row
        if endpoint is not None:
            row = db.scalar(
                select(ChatThread)
                .join(ChatMessage, ChatMessage.thread_id == ChatThread.thread_id)
                .where(ChatThread.person_id.is_(None), ChatMessage.endpoint_id == endpoint.endpoint_id)
                .limit(1)
            )
            if row is not None:
                return row
        row = ChatThread(person_id=person_id)
        db.add(row)
        db.flush()
        return row

    def sync_person(self, person_id: int) -> list[int]:
        """Mirror PHONE identifiers into WhatsApp endpoints without making phone the identity."""
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
                endpoint = db.scalar(
                    select(ContactEndpoint).where(
                        ContactEndpoint.provider == "WHATSAPP",
                        ContactEndpoint.normalized_phone == ident.normalized_value,
                    )
                )
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
                    LOGGER.warning(
                        "Endpoint conflict endpoint_id=%s existing_person=%s candidate_person=%s",
                        endpoint.endpoint_id,
                        endpoint.person_id,
                        person_id,
                    )
                endpoint.last_seen_at = max(endpoint.last_seen_at, ident.last_seen_at)
                endpoint.source_listing_id = ident.listing_id or endpoint.source_listing_id
                endpoint.updated_at = utcnow()
        return created

    def _choose_endpoint(self, db, person_id: int | None, listing_id: int | None = None) -> ContactEndpoint | None:
        if person_id is None:
            return None
        endpoints = db.scalars(
            select(ContactEndpoint)
            .where(ContactEndpoint.person_id == person_id, ContactEndpoint.state == "ACTIVE")
            .order_by(desc(ContactEndpoint.is_preferred), desc(ContactEndpoint.last_seen_at))
        ).all()
        if listing_id is not None:
            for endpoint in endpoints:
                if endpoint.source_listing_id == int(listing_id):
                    return endpoint
        for endpoint in endpoints:
            if endpoint.is_preferred:
                return endpoint
        return endpoints[0] if endpoints else None

    def ensure_person_thread(self, person_id: int, listing_id: int | None = None) -> dict[str, Any]:
        self.sync_person(person_id)
        with self.database.session() as db:
            thread = self._get_thread(db, person_id=int(person_id))
            endpoint = self._choose_endpoint(db, int(person_id), listing_id)
            return {
                "thread_id": thread.thread_id,
                "endpoint": self._endpoint_payload(endpoint) if endpoint else None,
            }

    def person_summary(self, person_id: int, listing_id: int | None = None) -> dict[str, Any]:
        self.sync_person(person_id)
        with self.database.session() as db:
            thread = db.scalar(select(ChatThread).where(ChatThread.person_id == int(person_id)))
            endpoints = db.scalars(
                select(ContactEndpoint)
                .where(ContactEndpoint.person_id == int(person_id))
                .order_by(desc(ContactEndpoint.is_preferred), desc(ContactEndpoint.last_seen_at))
            ).all()
            selected = self._choose_endpoint(db, int(person_id), listing_id)
            last = None
            count = 0
            if thread:
                count = int(db.scalar(select(func.count(ChatMessage.message_id)).where(ChatMessage.thread_id == thread.thread_id)) or 0)
                last = db.scalar(select(ChatMessage).where(ChatMessage.thread_id == thread.thread_id).order_by(desc(ChatMessage.timestamp)).limit(1))
            return {
                "person_id": int(person_id),
                "thread_id": thread.thread_id if thread else None,
                "message_count": count,
                "unread_count": thread.unread_count if thread else 0,
                "last_message": self._message_payload(last) if last else None,
                "endpoints": [self._endpoint_payload(x) for x in endpoints],
                "selected_endpoint": self._endpoint_payload(selected) if selected else None,
            }

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
            if not endpoint.provider_address:
                endpoint.provider_address = endpoint.normalized_phone.lstrip("+") + "@s.whatsapp.net"
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
            jid = endpoint.provider_address
        self.events.publish({"type": "message", "thread_id": thread_id, "message_id": message_id})
        try:
            response = self.worker.command(
                "send_text",
                jid=jid,
                text=text,
                client_message_id=str(message_id),
                timeout=30,
            )
            provider_message_id = (response.get("payload") or {}).get("provider_message_id")
            with self.database.session() as db:
                message = db.get(ChatMessage, message_id)
                if message:
                    message.status = "SENT"
                    message.provider_message_id = provider_message_id
                    message.error = None
                    thread = db.get(ChatThread, message.thread_id)
                    if thread:
                        thread.last_message_at = message.timestamp
                        thread.updated_at = utcnow()
        except Exception as exc:
            with self.database.session() as db:
                message = db.get(ChatMessage, message_id)
                if message:
                    message.status = "FAILED"
                    message.error = str(exc)[:1000]
            self.events.publish({"type": "message_update", "message_id": message_id, "status": "FAILED"})
            raise
        self.events.publish({"type": "message_update", "message_id": message_id, "status": "SENT"})
        return self.message(message_id)

    def retry(self, message_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None or message.direction != "OUT":
                raise KeyError(message_id)
            thread_id, text, endpoint_id, reply_to = message.thread_id, message.text or "", message.endpoint_id, message.reply_to_message_id
        return self.send_text(thread_id, text, endpoint_id, reply_to)

    def presence(self, thread_id: int, state: str, endpoint_id: int | None = None) -> dict[str, Any]:
        if not self.typing_enabled:
            return {"ok": True}
        if state not in {"composing", "paused", "recording", "available", "unavailable"}:
            raise ValueError("Ungültiger Presence-State")
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            endpoint = db.get(ContactEndpoint, int(endpoint_id)) if endpoint_id else self._choose_endpoint(db, thread.person_id if thread else None)
            if thread is None or endpoint is None:
                raise ValueError("Kein Endpoint")
            jid = endpoint.provider_address or endpoint.normalized_phone.lstrip("+") + "@s.whatsapp.net"
        self.worker.command("presence", jid=jid, state=state, timeout=5)
        return {"ok": True}

    def subscribe_presence(self, thread_id: int) -> None:
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            endpoint = self._choose_endpoint(db, thread.person_id if thread else None)
            if endpoint is not None:
                jid = endpoint.provider_address or endpoint.normalized_phone.lstrip("+") + "@s.whatsapp.net"
                try:
                    self.worker.command("subscribe_presence", jid=jid, timeout=5)
                except Exception:
                    LOGGER.debug("Presence subscription failed thread_id=%s", thread_id, exc_info=True)

    def _worker_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "message":
            try:
                self._ingest_message(event.get("payload") or {})
            except Exception:
                LOGGER.exception("Failed to ingest WhatsApp message")
        elif event_type == "message_update":
            payload = event.get("payload") or {}
            provider_id = payload.get("provider_message_id")
            status = payload.get("status")
            if provider_id and status in {"SENT", "DELIVERED", "READ", "FAILED"}:
                with self.database.session() as db:
                    message = db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == provider_id))
                    if message is not None:
                        message.status = status
        self.events.publish(event)

    def _ingest_message(self, payload: dict[str, Any]) -> None:
        provider_id = payload.get("provider_message_id")
        if not provider_id:
            return
        with self.database.session() as db:
            if db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == provider_id)) is not None:
                return
            jid = payload.get("jid")
            phone = jid_phone(jid)
            if not phone:
                return
            endpoint = db.scalar(
                select(ContactEndpoint).where(
                    ContactEndpoint.provider == "WHATSAPP",
                    ContactEndpoint.normalized_phone == phone,
                )
            )
            if endpoint is None:
                person_ids = set(db.scalars(
                    select(PersonIdentifier.person_id).where(
                        PersonIdentifier.kind == "PHONE",
                        PersonIdentifier.normalized_value == phone,
                    )
                ).all())
                person_id = next(iter(person_ids)) if len(person_ids) == 1 else None
                endpoint = ContactEndpoint(
                    person_id=person_id,
                    normalized_phone=phone,
                    display_phone=payload.get("display_phone"),
                    provider_address=jid,
                    state="ACTIVE",
                )
                db.add(endpoint)
                db.flush()
            else:
                endpoint.provider_address = jid or endpoint.provider_address
                endpoint.last_seen_at = utcnow()
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
            event = {"type": "message", "thread_id": thread.thread_id, "message_id": message.message_id}
        self.events.publish(event)

    def threads(self, filter_name: str = "ALL") -> list[dict[str, Any]]:
        with self.database.session() as db:
            rows = db.scalars(select(ChatThread).order_by(desc(ChatThread.last_message_at), desc(ChatThread.updated_at))).all()
            out: list[dict[str, Any]] = []
            for thread in rows:
                if filter_name == "UNREAD" and not thread.unread_count:
                    continue
                if filter_name == "UNASSIGNED" and thread.person_id is not None:
                    continue
                last = db.scalar(select(ChatMessage).where(ChatMessage.thread_id == thread.thread_id).order_by(desc(ChatMessage.timestamp)).limit(1))
                out.append(self._thread_payload(db, thread, last))
            return out

    def thread(self, thread_id: int, limit: int = 100) -> dict[str, Any]:
        limit = max(1, min(200, int(limit)))
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            messages = db.scalars(
                select(ChatMessage).where(ChatMessage.thread_id == thread.thread_id).order_by(desc(ChatMessage.timestamp)).limit(limit)
            ).all()
            messages.reverse()
            endpoints = db.scalars(select(ContactEndpoint).where(ContactEndpoint.person_id == thread.person_id).order_by(desc(ContactEndpoint.last_seen_at))).all() if thread.person_id else []
            payload = self._thread_payload(db, thread, messages[-1] if messages else None)
            payload["messages"] = [self._message_payload(x) for x in messages]
            payload["endpoints"] = [self._endpoint_payload(x) for x in endpoints]
        self.subscribe_presence(thread_id)
        return payload

    def message(self, message_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            row = db.get(ChatMessage, int(message_id))
            if row is None:
                raise KeyError(message_id)
            return self._message_payload(row)

    def mark_read(self, thread_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            thread.unread_count = 0
            thread.last_read_at = utcnow()
            return {"thread_id": thread.thread_id, "unread_count": 0}

    def unassigned(self) -> list[dict[str, Any]]:
        return self.threads("UNASSIGNED")

    def assignment_candidates(self, endpoint_id: int) -> list[dict[str, Any]]:
        with self.database.session() as db:
            endpoint = db.get(ContactEndpoint, int(endpoint_id))
            if endpoint is None:
                raise KeyError(endpoint_id)
            phone_matches = set(db.scalars(select(PersonIdentifier.person_id).where(PersonIdentifier.kind == "PHONE", PersonIdentifier.normalized_value == endpoint.normalized_phone)).all())
            rows = db.scalars(select(Person).order_by(desc(Person.last_seen_at)).limit(50)).all()
            out = []
            for person in rows:
                evidence = []
                if person.person_id in phone_matches:
                    evidence.append({"kind": "PHONE", "decision": "MATCH", "text": "Gleiche normalisierte Telefonnummer"})
                listing = db.scalar(select(Listing).where(Listing.person_id == person.person_id).order_by(desc(Listing.last_seen_at)).limit(1))
                if listing and listing.match_decision in {"MATCH", "STRONG_CANDIDATE", "CANDIDATE"}:
                    evidence.append({"kind": "IDENTITY", "decision": listing.match_decision, "text": listing.match_reason})
                if evidence:
                    out.append({"person_id": person.person_id, "display_name": person.display_name, "evidence": evidence})
            return out

    def assign_endpoint(self, endpoint_id: int, person_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            endpoint = db.get(ContactEndpoint, int(endpoint_id))
            if endpoint is None:
                raise KeyError(endpoint_id)
            if db.get(Person, int(person_id)) is None:
                raise KeyError(person_id)
            self._assign_endpoint_in_session(db, endpoint, int(person_id))
            db.flush()
            return self._endpoint_payload(endpoint)

    def _assign_endpoint_in_session(self, db, endpoint: ContactEndpoint, person_id: int) -> None:
        target = self._get_thread(db, person_id=person_id)
        temporary = db.scalar(
            select(ChatThread)
            .join(ChatMessage, ChatMessage.thread_id == ChatThread.thread_id)
            .where(ChatThread.person_id.is_(None), ChatMessage.endpoint_id == endpoint.endpoint_id)
            .limit(1)
        )
        if temporary and temporary.thread_id != target.thread_id:
            messages = db.scalars(select(ChatMessage).where(ChatMessage.thread_id == temporary.thread_id)).all()
            for message in messages:
                message.thread_id = target.thread_id
            target.unread_count += temporary.unread_count
            times = [x for x in (target.last_message_at, temporary.last_message_at) if x]
            if times:
                target.last_message_at = max(times)
            db.delete(temporary)
        endpoint.person_id = person_id
        endpoint.updated_at = utcnow()
        identifier = db.scalar(select(PersonIdentifier).where(
            PersonIdentifier.person_id == person_id,
            PersonIdentifier.kind == "PHONE",
            PersonIdentifier.normalized_value == endpoint.normalized_phone,
        ))
        if identifier is None:
            db.add(PersonIdentifier(
                person_id=person_id,
                kind="PHONE",
                normalized_value=endpoint.normalized_phone,
                raw_value=endpoint.display_phone,
            ))
        db.add(ChatMessage(
            thread_id=target.thread_id,
            endpoint_id=endpoint.endpoint_id,
            direction="SYSTEM",
            message_type="SYSTEM",
            text=f"Nummer {endpoint.display_phone or endpoint.normalized_phone} dieser Person zugeordnet.",
            status="RECEIVED",
        ))

    def merge_persons(self, db, source_person_id: int, target_person_id: int) -> None:
        for endpoint in db.scalars(select(ContactEndpoint).where(ContactEndpoint.person_id == source_person_id)).all():
            endpoint.person_id = target_person_id
            endpoint.updated_at = utcnow()
        source = db.scalar(select(ChatThread).where(ChatThread.person_id == source_person_id))
        target = db.scalar(select(ChatThread).where(ChatThread.person_id == target_person_id))
        if source is None:
            return
        if target is None:
            source.person_id = target_person_id
            return
        if source.thread_id == target.thread_id:
            return
        for message in db.scalars(select(ChatMessage).where(ChatMessage.thread_id == source.thread_id)).all():
            message.thread_id = target.thread_id
        target.unread_count += source.unread_count
        times = [x for x in (target.last_message_at, source.last_message_at) if x]
        if times:
            target.last_message_at = max(times)
        db.delete(source)

    def _avatar(self, db, person_id: int | None) -> str | None:
        if person_id is None:
            return None
        listing = db.scalar(select(Listing).where(Listing.person_id == person_id).order_by(desc(Listing.last_seen_at)).limit(1))
        if not listing or not listing.current_snapshot_id:
            return None
        media = db.scalar(select(SnapshotMedia).where(SnapshotMedia.snapshot_id == listing.current_snapshot_id, SnapshotMedia.kind == "IMAGE").order_by(SnapshotMedia.position).limit(1))
        if not media:
            return None
        if media.media_asset_id:
            asset = db.get(MediaAsset, media.media_asset_id)
            if asset:
                return f"/archive/{asset.local_path}"
        return media.remote_url

    def _thread_payload(self, db, thread: ChatThread, last: ChatMessage | None) -> dict[str, Any]:
        person = db.get(Person, thread.person_id) if thread.person_id else None
        endpoint = None
        if thread.person_id:
            endpoint = self._choose_endpoint(db, thread.person_id)
        elif last and last.endpoint_id:
            endpoint = db.get(ContactEndpoint, last.endpoint_id)
        return {
            "thread_id": thread.thread_id,
            "person_id": thread.person_id,
            "display_name": person.display_name if person else (endpoint.display_phone if endpoint else "Unbekannte WhatsApp-Nummer"),
            "avatar_url": self._avatar(db, thread.person_id),
            "unread_count": thread.unread_count,
            "last_message_at": iso(thread.last_message_at),
            "last_message": self._message_payload(last) if last else None,
            "endpoint": self._endpoint_payload(endpoint) if endpoint else None,
            "unassigned": thread.person_id is None,
        }

    @staticmethod
    def _endpoint_payload(endpoint: ContactEndpoint | None) -> dict[str, Any] | None:
        if endpoint is None:
            return None
        return {
            "endpoint_id": endpoint.endpoint_id,
            "person_id": endpoint.person_id,
            "provider": endpoint.provider,
            "normalized_phone": endpoint.normalized_phone,
            "display_phone": endpoint.display_phone,
            "source_listing_id": endpoint.source_listing_id,
            "is_preferred": endpoint.is_preferred,
            "state": endpoint.state,
            "first_seen_at": iso(endpoint.first_seen_at),
            "last_seen_at": iso(endpoint.last_seen_at),
        }

    @staticmethod
    def _message_payload(message: ChatMessage | None) -> dict[str, Any] | None:
        if message is None:
            return None
        return {
            "message_id": message.message_id,
            "thread_id": message.thread_id,
            "endpoint_id": message.endpoint_id,
            "provider_message_id": message.provider_message_id,
            "direction": message.direction,
            "message_type": message.message_type,
            "text": message.text,
            "media": json.loads(message.media_json) if message.media_json else None,
            "timestamp": iso(message.timestamp),
            "status": message.status,
            "error": message.error,
            "reply_to_message_id": message.reply_to_message_id,
        }
