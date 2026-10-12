from __future__ import annotations

import base64
import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import desc, func, select

from ladies_v9.database import Database
from ladies_v9.models import AppSetting, Listing, Person, PersonIdentifier, utcnow
from .integration import install_identity_merge_listener
from .models import ChatMessage, ChatThread, ContactEndpoint
from .service import EventHub, MessagingService as BaseMessagingService, jid_phone
from .whatsapp_manager import BaileysWorkerManager

LOGGER = logging.getLogger("ladies_explorer.messaging")
TYPING_SETTING_KEY = "whatsapp_typing_enabled"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class MessagingService(BaseMessagingService):
    """V9.2 lifecycle and Baileys-7 compatibility refinements."""

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
                    LOGGER.warning(
                        "Endpoint conflict endpoint_id=%s existing_person=%s candidate_person=%s",
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

    def _ingest_message(self, payload: dict[str, Any]) -> None:
        """Ingest Baileys 7 messages without ever treating a LID as a phone."""
        payload = self._archive_incoming_media(payload)
        provider_id = payload.get("provider_message_id")
        if not provider_id:
            return
        provider_address = str(payload.get("jid") or "") or None
        phone_jid = str(payload.get("phone_jid") or "") or None
        phone = jid_phone(phone_jid) if phone_jid else (jid_phone(provider_address) if provider_address and provider_address.endswith("@s.whatsapp.net") else None)

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
                person_id = None
                if phone:
                    person_ids = set(db.scalars(select(PersonIdentifier.person_id).where(
                        PersonIdentifier.kind == "PHONE",
                        PersonIdentifier.normalized_value == phone,
                    )).all())
                    if len(person_ids) == 1:
                        person_id = next(iter(person_ids))
                endpoint = ContactEndpoint(
                    person_id=person_id,
                    normalized_phone=phone,
                    display_phone=payload.get("display_phone") or (phone if phone else "Nummer nicht verfügbar"),
                    provider_address=provider_address,
                    state="ACTIVE",
                )
                db.add(endpoint)
                db.flush()
            else:
                if phone and not endpoint.normalized_phone:
                    endpoint.normalized_phone = phone
                if provider_address:
                    endpoint.provider_address = provider_address
                endpoint.last_seen_at = utcnow()
                endpoint.updated_at = utcnow()
                if endpoint.person_id is None and phone:
                    person_ids = set(db.scalars(select(PersonIdentifier.person_id).where(
                        PersonIdentifier.kind == "PHONE",
                        PersonIdentifier.normalized_value == phone,
                    )).all())
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
            event = {"type": "message", "thread_id": thread.thread_id, "message_id": message.message_id}
        self.events.publish(event)

    def _assign_endpoint_in_session(self, db, endpoint: ContactEndpoint, person_id: int, *, add_system_message: bool = True) -> None:
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
        endpoint.updated_at = utcnow()
        if endpoint.normalized_phone:
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
            exact_ids: set[int] = set()
            if endpoint.normalized_phone:
                exact_ids = set(db.scalars(select(PersonIdentifier.person_id).where(
                    PersonIdentifier.kind == "PHONE",
                    PersonIdentifier.normalized_value == endpoint.normalized_phone,
                )).all())
            persons = db.scalars(select(Person).order_by(desc(Person.last_seen_at)).limit(50)).all()
            out: list[dict[str, Any]] = []
            for person in persons:
                evidence = []
                if person.person_id in exact_ids:
                    evidence.append({"kind": "PHONE", "decision": "MATCH", "text": "Gleiche normalisierte Telefonnummer"})
                else:
                    listing = db.scalar(select(Listing).where(Listing.person_id == person.person_id).order_by(desc(Listing.last_seen_at)).limit(1))
                    if listing and listing.match_decision in {"STRONG_CANDIDATE", "CANDIDATE"}:
                        evidence.append({"kind": "IDENTITY_CONTEXT", "decision": listing.match_decision, "text": listing.match_reason or "Vorhandene Identity-Evidence der Person"})
                    else:
                        evidence.append({"kind": "MANUAL", "decision": "NO_EVIDENCE", "text": "Keine automatische Evidence für diese Nummer – nur manuelle Auswahl"})
                out.append({
                    "person_id": person.person_id,
                    "display_name": person.display_name,
                    "evidence": evidence,
                    "exact_phone_match": person.person_id in exact_ids,
                })
            out.sort(key=lambda x: (not x["exact_phone_match"], x["display_name"] or ""))
            return out
