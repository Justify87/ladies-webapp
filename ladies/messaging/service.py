from __future__ import annotations

import json
import logging
import queue
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import and_, desc, func, or_, select, update

from ladies_v9.database import Database
from ladies_v9.identity import normalize_phone_number
from ladies_v9.models import (
    AppSetting,
    Listing,
    MediaAsset,
    Person,
    PersonIdentifier,
    PersonUserState,
    SnapshotMedia,
    utcnow,
)

from .gateway import WhatsAppGateway
from .history_cache import REHYDRATE_LIMIT, RecentHistoryCache
from .integration import install_identity_merge_listener
from .media_store import MessagingMediaStore
from .models import ChatMessage, ChatMessageMeta, ChatReaction, ChatThread, ContactEndpoint

LOGGER = logging.getLogger("ladies_explorer.messaging")
TYPING_SETTING_KEY = "whatsapp_typing_enabled"
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


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


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


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[queue.Queue] = set()
        import threading
        self._lock = threading.Lock()

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
    """Canonical person-centric WhatsApp service.

    The V9.6 service intentionally composes the worker gateway, media store and
    recent-history cache instead of layering release-specific subclasses.
    """

    def __init__(self, database: Database, data_dir: str | Path):
        install_identity_merge_listener()
        if not isinstance(database, Database):
            raise TypeError("Messaging benötigt die gemeinsame Application-Database")
        self.database = database

        self.events = EventHub()
        self.data_dir = Path(data_dir)
        self.auth_dir = self.data_dir / "whatsapp" / "auth"
        self.media_store = MessagingMediaStore(self.data_dir)
        self.media_dir = self.media_store.media_dir
        self.view_once_dir = self.media_store.view_once_dir
        self.history_cache = RecentHistoryCache(self.data_dir)

        with self.database.session() as db:
            setting = db.get(AppSetting, TYPING_SETTING_KEY)
            self.typing_enabled = setting is None or setting.value_json.strip().lower() != "false"

        root = Path(__file__).resolve().parent
        self.gateway = WhatsAppGateway(root / "whatsapp_worker", self.auth_dir, self._worker_event)
        self.worker = self.gateway
        ok, detail = self.gateway.dependency_status()
        if not ok:
            self.gateway.state = "DEPENDENCY_MISSING"
            self.gateway.detail = detail
        elif (self.auth_dir / "creds.json").exists():
            self.gateway.start()
        else:
            self.gateway.state = "NOT_CONFIGURED"

    def status(self) -> dict[str, Any]:
        out = dict(self.gateway.status())
        out["typing_enabled"] = self.typing_enabled
        return out

    def connect(self) -> dict[str, Any]:
        ok, detail = self.gateway.dependency_status()
        if not ok:
            self.gateway.state = "DEPENDENCY_MISSING"
            self.gateway.detail = detail
            return self.status()
        try:
            if self.gateway.proc and self.gateway.proc.poll() is None:
                self.gateway.command("connect", timeout=15)
            else:
                self.gateway.start()
        except Exception as exc:
            LOGGER.warning("WhatsApp connect failed: %s", exc)
        return self.status()

    def disconnect(self) -> dict[str, Any]:
        try:
            if self.gateway.proc and self.gateway.proc.poll() is None:
                self.gateway.command("disconnect", timeout=10)
        except Exception:
            LOGGER.exception("WhatsApp disconnect failed")
        return self.status()

    def logout(self) -> dict[str, Any]:
        try:
            if self.gateway.proc and self.gateway.proc.poll() is None:
                self.gateway.command("logout", timeout=10)
        except Exception:
            LOGGER.exception("WhatsApp logout failed")
        try:
            shutil.rmtree(self.auth_dir, ignore_errors=True)
            self.auth_dir.mkdir(parents=True, exist_ok=True)
        finally:
            self.gateway.qr = None
            self.gateway.account = None
            self.gateway.state = "LOGGED_OUT"
        return self.status()

    def qr(self) -> str | None:
        return self.gateway.qr

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

    @staticmethod
    def _resolved_endpoint(db, thread: ChatThread, endpoint_id: int | None, chooser) -> ContactEndpoint:
        endpoint = db.get(ContactEndpoint, int(endpoint_id)) if endpoint_id else chooser(db, thread.person_id)
        if endpoint is None or thread.person_id is None or endpoint.person_id != thread.person_id or endpoint.state != "ACTIVE":
            raise ValueError("Kein gültiger WhatsApp-Endpunkt ausgewählt")
        return endpoint

    @staticmethod
    def _validate_reply_target(db, thread: ChatThread, endpoint: ContactEndpoint, reply_to: int | None) -> None:
        if reply_to is None:
            return
        quoted = db.get(ChatMessage, int(reply_to))
        if quoted is None or quoted.thread_id != thread.thread_id:
            raise ValueError("Antwort-Ziel gehört nicht zu diesem Chat")
        if quoted.endpoint_id != endpoint.endpoint_id:
            raise ValueError("Antworten sind nur innerhalb derselben WhatsApp-Nummer möglich")

    def ensure_person_thread(self, person_id: int, listing_id: int | None = None) -> dict[str, Any]:
        self.sync_person(person_id)
        with self.database.session() as db:
            thread = self._get_thread(db, person_id=int(person_id))
            endpoint = self._choose_endpoint(db, int(person_id), listing_id)
            return {"thread_id": thread.thread_id, "endpoint": self._endpoint_payload(endpoint) if endpoint else None}

    def person_summary(self, person_id: int, listing_id: int | None = None) -> dict[str, Any]:
        self.sync_person(person_id)
        with self.database.session() as db:
            thread = db.scalar(select(ChatThread).where(ChatThread.person_id == int(person_id)))
            endpoints = db.scalars(
                select(ContactEndpoint).where(ContactEndpoint.person_id == int(person_id)).order_by(desc(ContactEndpoint.is_preferred), desc(ContactEndpoint.last_seen_at))
            ).all()
            selected = self._choose_endpoint(db, int(person_id), listing_id)
            last = None
            count = 0
            if thread:
                count = int(db.scalar(select(func.count(ChatMessage.message_id)).where(ChatMessage.thread_id == thread.thread_id)) or 0)
                last = db.scalar(select(ChatMessage).where(ChatMessage.thread_id == thread.thread_id).order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id)).limit(1))
            return {
                "person_id": int(person_id), "thread_id": thread.thread_id if thread else None,
                "message_count": count, "unread_count": thread.unread_count if thread else 0,
                "last_message": self._message_payload(last) if last else None,
                "endpoints": [self._endpoint_payload(x) for x in endpoints],
                "selected_endpoint": self._endpoint_payload(selected) if selected else None,
            }

    def _resolve_discovered_phone(self, db, endpoint: ContactEndpoint, phone: str | None, display_phone: str | None = None) -> ContactEndpoint:
        if not phone:
            return endpoint
        phone_endpoint = db.scalar(select(ContactEndpoint).where(
            ContactEndpoint.provider == "WHATSAPP", ContactEndpoint.normalized_phone == phone,
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
            LOGGER.warning("Discovered phone conflicts with person identity endpoint_id=%s person_ids=%s", endpoint.endpoint_id, sorted(people))
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
            provider_address = endpoint.provider_address if endpoint.provider_address and (not phone_endpoint.provider_address or endpoint.provider_address.endswith("@lid")) else None
            db.execute(update(ChatMessage).where(ChatMessage.endpoint_id == endpoint.endpoint_id).values(endpoint_id=phone_endpoint.endpoint_id))
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
        if quoted is None or quoted.thread_id != message.thread_id:
            raise ValueError("Antwort-Ziel gehört nicht zu diesem Chat")
        if quoted.endpoint_id != message.endpoint_id:
            raise ValueError("Antworten sind nur innerhalb derselben WhatsApp-Nummer möglich")
        key = self._provider_key(quoted)
        if not key:
            return None
        media = _load(quoted.media_json, {}) or {}
        return {"key": key, "message_type": quoted.message_type, "text": quoted.text, "caption": media.get("caption"), "file_name": media.get("file_name")}

    def _save_send_result(self, message_id: int, response: dict[str, Any]) -> dict[str, Any]:
        payload = response.get("payload") or {}
        provider_id = payload.get("provider_message_id")
        if not provider_id:
            raise RuntimeError("WhatsApp lieferte keine Nachrichten-ID")
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None:
                raise KeyError(message_id)
            duplicate = db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == provider_id, ChatMessage.message_id != message.message_id))
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
        self.events.publish({"type": "message_update", "thread_id": result["thread_id"], "message_id": int(message_id), "status": result["status"]})
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
            response = self.gateway.command("send_text", jid=jid, text=text, quote=quote, client_message_id=str(message_id), timeout=30)
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
            file_path = self.media_store.resolve(self.media_store.archive_name(media))
            meta = self._ensure_meta(db, message)
            quote = self._quote_command(db, message)
            command = {"jid": jid, "media_type": message.message_type, "file_path": str(file_path), "mime_type": media.get("mime_type"), "file_name": media.get("file_name"), "caption": message.text or media.get("caption"), "view_once": bool(meta.view_once), "quote": quote, "client_message_id": str(message_id)}
        try:
            response = self.gateway.command("send_media", timeout=90, **command)
            return self._save_send_result(message_id, response)
        except Exception as exc:
            self._mark_failed(message_id, exc)
            raise

    def send_text(self, thread_id: int, text: str, endpoint_id: int | None = None, reply_to: int | None = None) -> dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            raise ValueError("Nachricht ist leer")
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            endpoint = self._resolved_endpoint(db, thread, endpoint_id, self._choose_endpoint)
            self._validate_reply_target(db, thread, endpoint, reply_to)
            message = ChatMessage(thread_id=thread.thread_id, endpoint_id=endpoint.endpoint_id, direction="OUT", message_type="TEXT", text=text, status="PENDING", reply_to_message_id=reply_to)
            db.add(message)
            db.flush()
            message_id = message.message_id
        self.events.publish({"type": "message", "thread_id": int(thread_id), "message_id": message_id})
        return self._dispatch_text(message_id)

    def send_media(self, thread_id: int, *, archive_name: str, media_type: str, mime_type: str | None = None, file_name: str | None = None, caption: str | None = None, endpoint_id: int | None = None, reply_to: int | None = None, view_once: bool = False) -> dict[str, Any]:
        media_type = str(media_type or "").upper()
        if media_type not in MEDIA_TYPES:
            raise ValueError("Nicht unterstützter Medientyp")
        if view_once and media_type not in VIEW_ONCE_TYPES:
            raise ValueError("Einmal ansehen ist für diesen Medientyp nicht verfügbar")
        if media_type == "VOICE":
            archive_name, mime_type = self.media_store.normalize_voice(archive_name)
            file_name = Path(file_name or "sprachnachricht.ogg").stem + ".ogg"
        target = self.media_store.resolve(archive_name)
        caption = str(caption or "").strip() or None
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            endpoint = self._resolved_endpoint(db, thread, endpoint_id, self._choose_endpoint)
            self._validate_reply_target(db, thread, endpoint, reply_to)
            media = {"archive_name": target.name, "local_url": f"/archive/whatsapp/media/{target.name}", "mime_type": mime_type or "application/octet-stream", "file_name": file_name, "caption": caption, "byte_size": target.stat().st_size}
            message = ChatMessage(thread_id=thread.thread_id, endpoint_id=endpoint.endpoint_id, direction="OUT", message_type=media_type, text=caption, media_json=_json(media), status="PENDING", reply_to_message_id=reply_to)
            db.add(message)
            db.flush()
            db.add(ChatMessageMeta(message_id=message.message_id, view_once=bool(view_once)))
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

    def presence(self, thread_id: int, state: str, endpoint_id: int | None = None) -> dict[str, Any]:
        if not self.typing_enabled:
            return {"ok": True}
        if state not in {"composing", "paused", "recording", "available", "unavailable"}:
            raise ValueError("Ungültiger Presence-State")
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            endpoint = self._resolved_endpoint(db, thread, endpoint_id, self._choose_endpoint)
            jid = self._endpoint_jid(endpoint)
        self.gateway.command("presence", jid=jid, state=state, timeout=5)
        return {"ok": True}

    def subscribe_presence(self, thread_id: int) -> None:
        if self.gateway.state != "CONNECTED":
            return
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            endpoint = self._choose_endpoint(db, thread.person_id if thread else None)
            if endpoint is None:
                return
            jid = self._endpoint_jid(endpoint)
        try:
            self.gateway.command("subscribe_presence", jid=jid, timeout=5)
        except Exception:
            LOGGER.debug("Presence subscription failed thread_id=%s", thread_id, exc_info=True)

    def _ingest_message(self, payload: dict[str, Any]) -> None:
        self.history_cache.store([payload])
        provider_id = payload.get("provider_message_id")
        if not provider_id:
            return
        with self.database.session() as db:
            if db.scalar(select(ChatMessage.message_id).where(ChatMessage.provider_message_id == str(provider_id))) is not None:
                return
        payload = self.media_store.archive_incoming(payload)
        provider_address = str(payload.get("jid") or "") or None
        phone_jid = str(payload.get("phone_jid") or "") or None
        phone = jid_phone(phone_jid) if phone_jid else (jid_phone(provider_address) if provider_address and provider_address.endswith("@s.whatsapp.net") else None)
        quote = payload.get("quote") if isinstance(payload.get("quote"), dict) else None
        with self.database.session() as db:
            endpoint = None
            if provider_address:
                endpoint = db.scalar(select(ContactEndpoint).where(ContactEndpoint.provider == "WHATSAPP", ContactEndpoint.provider_address == provider_address))
            if endpoint is None and phone:
                endpoint = db.scalar(select(ContactEndpoint).where(ContactEndpoint.provider == "WHATSAPP", ContactEndpoint.normalized_phone == phone))
            if endpoint is None:
                person_ids = self._phone_person_ids(db, phone)
                person_id = next(iter(person_ids)) if len(person_ids) == 1 else None
                endpoint = ContactEndpoint(person_id=person_id, normalized_phone=phone, display_phone=payload.get("display_phone") or (phone if phone else "Nummer nicht verfügbar"), provider_address=provider_address, state="ACTIVE")
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
            message = ChatMessage(thread_id=thread.thread_id, endpoint_id=endpoint.endpoint_id, provider_message_id=str(provider_id), direction="OUT" if payload.get("from_me") else "IN", message_type=payload.get("message_type") or "TEXT", text=payload.get("text"), media_json=_json(payload.get("media")), timestamp=timestamp, provider_timestamp=timestamp, status="SENT" if payload.get("from_me") else "RECEIVED")
            db.add(message)
            db.flush()
            meta = self._ensure_meta(db, message)
            if payload.get("provider_key"):
                meta.provider_key_json = _json(payload.get("provider_key"))
            meta.view_once = bool(payload.get("view_once"))
            if quote:
                meta.quote_provider_message_id = quote.get("provider_message_id")
                meta.quote_snapshot_json = _json(quote.get("snapshot"))
                if quote.get("provider_message_id"):
                    quoted = db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == quote.get("provider_message_id")))
                    if quoted is not None and quoted.thread_id == message.thread_id:
                        message.reply_to_message_id = quoted.message_id
            meta.updated_at = utcnow()
            thread.last_message_at = timestamp
            thread.updated_at = utcnow()
            if not payload.get("from_me") and not payload.get("rehydrated"):
                thread.unread_count += 1
            browser_event = {"type": "message", "thread_id": thread.thread_id, "message_id": message.message_id}
        self.events.publish(browser_event)

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
            existing = db.scalar(select(ChatReaction).where(ChatReaction.message_id == message.message_id, ChatReaction.actor == actor))
            emoji = str(payload.get("emoji") or "")[:32]
            if not emoji:
                if existing is not None:
                    db.delete(existing)
            elif existing is None:
                db.add(ChatReaction(message_id=message.message_id, actor=actor, emoji=emoji, provider_key_json=_json(payload.get("reaction_key")), created_at=_utc_from_ms(payload.get("timestamp_ms")), updated_at=utcnow()))
            else:
                existing.emoji = emoji
                existing.provider_key_json = _json(payload.get("reaction_key"))
                existing.updated_at = utcnow()
            browser_event = {"type": "reaction_update", "thread_id": message.thread_id, "message_id": message.message_id}
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
                browser_event = {"type": "message_update", "thread_id": message.thread_id, "message_id": message.message_id, "status": "PLAYED"}
        if browser_event:
            self.events.publish(browser_event)

    def _ingest_content_update(self, payload: dict[str, Any]) -> None:
        provider_id = payload.get("provider_message_id")
        action = str(payload.get("action") or "").upper()
        if not provider_id or action not in {"EDIT", "DELETE"}:
            return
        browser_event = None
        with self.database.session() as db:
            message = db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == provider_id))
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
            meta.updated_at = now
            browser_event = {"type": "message_update", "thread_id": message.thread_id, "message_id": message.message_id, "status": message.status}
        if browser_event:
            self.events.publish(browser_event)

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
                endpoint = db.scalar(select(ContactEndpoint).where(ContactEndpoint.provider == "WHATSAPP", ContactEndpoint.provider_address.in_(addresses)).limit(1))
            if endpoint is None and phones:
                endpoint = db.scalar(select(ContactEndpoint).where(ContactEndpoint.provider == "WHATSAPP", ContactEndpoint.normalized_phone.in_(phones)).limit(1))
            if endpoint is None:
                return None
            if endpoint.person_id is not None:
                thread = db.scalar(select(ChatThread).where(ChatThread.person_id == endpoint.person_id))
            else:
                thread = db.scalar(select(ChatThread).join(ChatMessage, ChatMessage.thread_id == ChatThread.thread_id).where(ChatThread.person_id.is_(None), ChatMessage.endpoint_id == endpoint.endpoint_id).limit(1))
            if thread is None:
                return None
            return {"type": "presence", "thread_id": thread.thread_id, "endpoint_id": endpoint.endpoint_id, "person_id": endpoint.person_id, "state": state}

    def _worker_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "history_seed":
            self.history_cache.store(event.get("payloads") or [])
            return
        if event_type == "message":
            try:
                self._ingest_message(event.get("payload") or {})
            except Exception:
                LOGGER.exception("Failed to ingest WhatsApp message")
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
            payload = event.get("payload") or {}
            if payload.get("status") == "PLAYED":
                self._update_played(payload)
                return
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
                        conflict = db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == provider_id, ChatMessage.message_id != local.message_id))
                        if conflict is None:
                            local.provider_message_id = provider_id
                            message = local
                if message is not None:
                    if status == "FAILED" or STATUS_RANK.get(status, 0) >= STATUS_RANK.get(message.status, 0):
                        message.status = status
                    browser_event = {"type": "message_update", "thread_id": message.thread_id, "message_id": message.message_id, "status": message.status}
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
            self.events.publish({"type": "connection", "state": state, "detail": event.get("detail")})
            if state == "CONNECTED":
                try:
                    self.flush_pending_read_receipts()
                except Exception:
                    LOGGER.debug("Pending read receipt flush failed", exc_info=True)
            return
        if event_type == "qr":
            self.events.publish({"type": "qr_available"})
            return
        if event_type == "error":
            self.events.publish({"type": "worker_error", "code": str(event.get("code") or "WORKER_ERROR")[:80]})

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
                query = query.where(or_(ChatMessage.timestamp < cursor.timestamp, and_(ChatMessage.timestamp == cursor.timestamp, ChatMessage.message_id < cursor.message_id)))
            rows = db.scalars(query.order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id)).limit(limit + 1)).all()
            has_more = len(rows) > limit
            rows = rows[:limit]
            rows.reverse()
            endpoints = db.scalars(select(ContactEndpoint).where(ContactEndpoint.person_id == thread.person_id).order_by(desc(ContactEndpoint.last_seen_at))).all() if thread.person_id else []
            latest = db.scalar(select(ChatMessage).where(ChatMessage.thread_id == thread.thread_id).order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id)).limit(1))
            payload = self._thread_payload(db, thread, latest)
            payload["messages"] = [self._message_payload(x) for x in rows]
            payload["endpoints"] = [self._endpoint_payload(x) for x in endpoints]
            payload["has_more"] = has_more
            payload["next_before"] = rows[0].message_id if has_more and rows else None
        if before_message_id is None:
            self.subscribe_presence(thread_id)
        return payload

    def threads(self, filter_name: str = "ALL") -> list[dict[str, Any]]:
        if filter_name == "UNASSIGNED":
            with self.database.session() as db:
                rows = db.scalars(select(ChatThread).where(ChatThread.person_id.is_(None)).order_by(desc(ChatThread.last_message_at), desc(ChatThread.updated_at))).all()
                out = []
                for thread in rows:
                    last = db.scalar(select(ChatMessage).where(ChatMessage.thread_id == thread.thread_id).order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id)).limit(1))
                    if last is not None:
                        out.append(self._thread_payload(db, thread, last))
                return out
        with self.database.session() as db:
            rows = db.scalars(select(ChatThread).order_by(desc(ChatThread.last_message_at), desc(ChatThread.updated_at))).all()
            out: list[dict[str, Any]] = []
            for thread in rows:
                if thread.person_id is None:
                    continue
                last = db.scalar(select(ChatMessage).where(ChatMessage.thread_id == thread.thread_id).order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id)).limit(1))
                if last is None:
                    continue
                if filter_name == "UNREAD" and not thread.unread_count:
                    continue
                state = db.get(PersonUserState, int(thread.person_id))
                payload = self._thread_payload(db, thread, last)
                payload["favorite"] = bool(state.favorite) if state else False
                payload["preference"] = state.preference if state else None
                out.append(payload)
            out.sort(key=lambda item: (0 if item.get("favorite") else 1, 0 if item.get("preference") == "LIKE" else 1))
            return out

    def message(self, message_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            row = db.get(ChatMessage, int(message_id))
            if row is None:
                raise KeyError(message_id)
            return self._message_payload(row)

    def _read_keys_for_thread(self, db, thread_id: int, message_ids: set[int] | None = None):
        rows = db.scalars(select(ChatMessage).where(ChatMessage.thread_id == int(thread_id), ChatMessage.direction == "IN").order_by(ChatMessage.timestamp, ChatMessage.message_id)).all()
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
        if key_values and self.gateway.state == "CONNECTED":
            try:
                self.gateway.command("mark_read", keys=key_values, timeout=15)
                sent = True
            except Exception:
                LOGGER.debug("WhatsApp read receipt deferred thread_id=%s", thread_id, exc_info=True)
        if sent:
            now = utcnow()
            ids = {str(key.get("id")) for key in key_values if key.get("id")}
            with self.database.session() as db:
                rows = db.scalars(select(ChatMessage).where(ChatMessage.thread_id == int(thread_id), ChatMessage.direction == "IN")).all()
                for message in rows:
                    meta = message.extra
                    key = _load(meta.provider_key_json, None) if meta else None
                    if meta and key and str(key.get("id")) in ids:
                        meta.read_receipt_sent_at = now
                        meta.updated_at = now
        self.events.publish({"type": "read_update", "thread_id": int(thread_id)})
        return {"thread_id": int(thread_id), "unread_count": 0, "provider_receipts": len(key_values) if sent else 0}

    def flush_pending_read_receipts(self) -> int:
        if self.gateway.state != "CONNECTED":
            return 0
        pending: list[tuple[int, dict[str, Any]]] = []
        with self.database.session() as db:
            metas = db.scalars(select(ChatMessageMeta).where(ChatMessageMeta.read_by_me_at.is_not(None), ChatMessageMeta.read_receipt_sent_at.is_(None))).all()
            for meta in metas:
                message = db.get(ChatMessage, meta.message_id)
                key = _load(meta.provider_key_json, None)
                if message is not None and message.direction == "IN" and key:
                    pending.append((meta.message_id, key))
        count = 0
        for offset in range(0, len(pending), 100):
            batch = pending[offset:offset + 100]
            self.gateway.command("mark_read", keys=[key for _, key in batch], timeout=15)
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
        self.gateway.command("send_reaction", jid=jid, target_key=target_key, emoji=emoji, timeout=30)
        with self.database.session() as db:
            existing = db.scalar(select(ChatReaction).where(ChatReaction.message_id == int(message_id), ChatReaction.actor == "ME"))
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
        self.gateway.command("edit_message", jid=jid, target_key=key, text=text, timeout=30)
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None:
                raise KeyError(message_id)
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
        self.gateway.command("delete_message", jid=jid, target_key=key, timeout=30)
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None:
                raise KeyError(message_id)
            meta = self._ensure_meta(db, message)
            media = _load(message.media_json, {}) or {}
            self.media_store.delete(media, private=bool(meta.view_once))
            meta.deleted_at = utcnow()
            meta.updated_at = utcnow()
            message.text = None
            message.media_json = None
            message.status = "DELETED"
        self.events.publish({"type": "message_update", "thread_id": thread_id, "message_id": int(message_id), "status": "DELETED"})
        return self.message(int(message_id))

    def media_path(self, message_id: int) -> tuple[Path, str, str, bool]:
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None:
                raise KeyError(message_id)
            meta = self._ensure_meta(db, message)
            if meta.view_once:
                raise ValueError("Einmal-Medien werden nur über den Einmal-Endpunkt ausgeliefert")
            if meta.deleted_at is not None:
                raise FileNotFoundError(str(message_id))
            media = _load(message.media_json, {}) or {}
            return self.media_store.delivery(message.message_type, media, private=False)

    def view_once_path(self, message_id: int):
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
            source = self.media_store.resolve(self.media_store.archive_name(media), private=True, allow_legacy_media=True)
            mime = str(media.get("mime_type") or "application/octet-stream")
        import tempfile
        import shutil as _shutil
        spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
        try:
            with source.open("rb") as handle:
                _shutil.copyfileobj(handle, spool, length=1024 * 1024)
            spool.seek(0)
            now = utcnow()
            with self.database.session() as db:
                result = db.execute(update(ChatMessageMeta).where(ChatMessageMeta.message_id == int(message_id), ChatMessageMeta.view_once_consumed_at.is_(None)).values(view_once_consumed_at=now, updated_at=now))
                if result.rowcount != 1:
                    raise RuntimeError("Einmal-Medium wurde bereits geöffnet")
            source.unlink(missing_ok=True)
            return spool, mime
        except Exception:
            spool.close()
            raise

    def unassigned(self) -> list[dict[str, Any]]:
        return self.threads("UNASSIGNED")

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
                evidence = [{"kind": "PHONE" if exact else "MANUAL", "decision": "MATCH" if exact else "NO_EVIDENCE", "text": "Gleiche normalisierte Telefonnummer" if exact else "Keine automatische Evidence für diesen WhatsApp-Kontakt – manuelle Auswahl"}]
                out.append({"person_id": person.person_id, "display_name": person.display_name, "evidence": evidence, "exact_phone_match": exact})
            out.sort(key=lambda x: (not x["exact_phone_match"], (x["display_name"] or "").lower()))
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

    def _assign_endpoint_in_session(self, db, endpoint: ContactEndpoint, person_id: int, *, add_system_message: bool = True) -> None:
        person_id = int(person_id)
        if endpoint.normalized_phone:
            other_people = self._phone_person_ids(db, endpoint.normalized_phone) - {person_id}
            if other_people:
                endpoint.state = "IDENTITY_CONFLICT"
                raise ValueError("Telefonnummer ist bereits einer anderen Person zugeordnet")
        target = self._get_thread(db, person_id=person_id)
        temporary = db.scalar(select(ChatThread).join(ChatMessage, ChatMessage.thread_id == ChatThread.thread_id).where(ChatThread.person_id.is_(None), ChatMessage.endpoint_id == endpoint.endpoint_id).limit(1))
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
            db.add(ChatMessage(thread_id=target.thread_id, endpoint_id=endpoint.endpoint_id, direction="SYSTEM", message_type="SYSTEM", text=f"Nummer {label} dieser Person zugeordnet.", status="RECEIVED"))

    def _person_message_count(self, person_id: int) -> int:
        with self.database.session() as db:
            thread = db.scalar(select(ChatThread).where(ChatThread.person_id == int(person_id)))
            if thread is None:
                return 0
            return int(db.scalar(select(func.count(ChatMessage.message_id)).where(ChatMessage.thread_id == thread.thread_id, ChatMessage.direction.in_(("IN", "OUT")))) or 0)

    def rehydrate_recent_history(self, person_id: int, limit: int = REHYDRATE_LIMIT) -> dict[str, Any]:
        person_id = int(person_id)
        limit = max(1, min(REHYDRATE_LIMIT, int(limit)))
        self.sync_person(person_id)
        existing = self._person_message_count(person_id)
        if existing >= limit:
            return {"person_id": person_id, "existing": existing, "restored": 0, "requested": 0}
        with self.database.session() as db:
            endpoints = db.scalars(select(ContactEndpoint).where(ContactEndpoint.person_id == person_id, ContactEndpoint.state == "ACTIVE").order_by(desc(ContactEndpoint.is_preferred), desc(ContactEndpoint.last_seen_at))).all()
        restored = 0
        requested = 0
        for endpoint in endpoints:
            missing = max(0, limit - self._person_message_count(person_id))
            if not missing:
                break
            cached = self.history_cache.for_endpoint(endpoint)
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
            if not missing or self.gateway.state != "CONNECTED":
                continue
            anchor = next((row for row in reversed(cached) if isinstance(row.get("provider_key"), dict) and row["provider_key"].get("id")), None)
            if anchor is None:
                continue
            jid = endpoint.provider_address or (endpoint.normalized_phone.lstrip("+") + "@s.whatsapp.net" if endpoint.normalized_phone else None)
            if not jid:
                continue
            try:
                timestamp_ms = int(float(anchor.get("timestamp") or 0) * 1000)
                if timestamp_ms <= 0:
                    continue
                self.gateway.command("fetch_recent_history", jid=jid, count=missing, oldest_key=anchor["provider_key"], oldest_timestamp_ms=timestamp_ms, timeout=10)
                requested += missing
            except Exception as exc:
                LOGGER.info("Recent WhatsApp history request skipped person_id=%s endpoint_id=%s: %s", person_id, endpoint.endpoint_id, exc)
        return {"person_id": person_id, "existing": self._person_message_count(person_id), "restored": restored, "requested": requested}

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
        endpoint = self._choose_endpoint(db, thread.person_id) if thread.person_id else (db.get(ContactEndpoint, last.endpoint_id) if last and last.endpoint_id else None)
        return {"thread_id": thread.thread_id, "person_id": thread.person_id, "display_name": person.display_name if person else (endpoint.display_phone if endpoint else "Unbekannte WhatsApp-Nummer"), "avatar_url": self._avatar(db, thread.person_id), "unread_count": thread.unread_count, "last_message_at": iso(thread.last_message_at), "last_message": self._message_payload(last) if last else None, "endpoint": self._endpoint_payload(endpoint) if endpoint else None, "unassigned": thread.person_id is None}

    @staticmethod
    def _endpoint_payload(endpoint: ContactEndpoint | None) -> dict[str, Any] | None:
        if endpoint is None:
            return None
        return {"endpoint_id": endpoint.endpoint_id, "person_id": endpoint.person_id, "provider": endpoint.provider, "normalized_phone": endpoint.normalized_phone, "display_phone": endpoint.display_phone, "source_listing_id": endpoint.source_listing_id, "is_preferred": endpoint.is_preferred, "state": endpoint.state, "first_seen_at": iso(endpoint.first_seen_at), "last_seen_at": iso(endpoint.last_seen_at)}

    @staticmethod
    def _message_payload(message: ChatMessage | None) -> dict[str, Any] | None:
        if message is None:
            return None
        meta = message.extra
        payload: dict[str, Any] = {
            "message_id": message.message_id, "thread_id": message.thread_id, "endpoint_id": message.endpoint_id,
            "provider_message_id": message.provider_message_id, "direction": message.direction,
            "message_type": message.message_type, "text": message.text, "media": _load(message.media_json, None),
            "timestamp": iso(message.timestamp), "status": message.status, "error": message.error,
            "reply_to_message_id": message.reply_to_message_id,
            "quote_snapshot": _load(meta.quote_snapshot_json, None) if meta else None,
            "view_once": bool(meta.view_once) if meta else False,
            "view_once_consumed": bool(meta and meta.view_once_consumed_at),
            "edited_at": iso(meta.edited_at) if meta else None, "deleted_at": iso(meta.deleted_at) if meta else None,
            "read_by_me_at": iso(meta.read_by_me_at) if meta else None,
            "reactions": [{"actor": row.actor, "emoji": row.emoji, "updated_at": iso(row.updated_at)} for row in sorted(message.reactions or [], key=lambda item: item.actor)],
        }
        media = payload.get("media")
        if isinstance(media, dict):
            has_stored_file = bool(MessagingMediaStore.archive_name(media))
            clean = dict(media)
            for key in ("archive_name", "sha256", "private_view_once", "local_url"):
                clean.pop(key, None)
            if payload["view_once"]:
                if payload["view_once_consumed"]:
                    clean = {"consumed": True, "mime_type": clean.get("mime_type")}
            elif has_stored_file and not payload["deleted_at"]:
                clean["local_url"] = f"/api/messaging/messages/{message.message_id}/media"
            payload["media"] = clean or None
        if payload["deleted_at"]:
            payload["text"] = None
            payload["media"] = None
        return payload
