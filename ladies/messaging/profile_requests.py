from __future__ import annotations

import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone

from flask import jsonify, request
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, desc, select
from sqlalchemy.orm import Mapped, mapped_column

from ladies_v9.errors import NotFoundError, ValidationError
from ladies_v9.models import Base, utcnow
from ladies_v9.request_parsing import read_int

from .scope import person_is_ladies_scoped, refresh_endpoint_identity

PROFILE_REQUEST_SOURCE = "profile_request"
PROFILE_REQUEST_COOLDOWN = timedelta(hours=24)

INTRO_VARIANTS = (
    "Hi 😊 ich hätte Interesse an einem Treffen.",
    "Hey 🙂 dein Profil hat mein Interesse geweckt.",
    "Hallo 😊 ich wollte mich mal unverbindlich melden.",
    "Hi 🙂 ich bin auf dein Profil aufmerksam geworden.",
    "Hey 😊 ich würde mich gern kurz erkundigen.",
)

PRICE_VARIANTS = (
    "Magst du mir kurz deine aktuellen Preise schicken?",
    "Wie sehen deine Preise aktuell aus?",
    "Was nimmst du aktuell für ein Treffen?",
    "Kannst du mir kurz deine Preise und Konditionen nennen?",
    "Magst du mir kurz sagen, was du aktuell für ein Treffen nimmst?",
)


class ProfileRequest(Base):
    """Persistent metadata for one automatic request initiated from a profile."""

    __tablename__ = "profile_requests"
    __table_args__ = (
        UniqueConstraint("request_token", name="uq_profile_request_token"),
        UniqueConstraint("message_id", name="uq_profile_request_message"),
        Index("idx_profile_request_person_sent", "person_id", "sent_at"),
    )

    request_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Deliberately not a Person FK: identity merges may delete a source person while
    # the linked chat/message history remains canonical and must stay inspectable.
    person_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    thread_id: Mapped[int | None] = mapped_column(ForeignKey("chat_threads.thread_id", ondelete="SET NULL"))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.message_id", ondelete="SET NULL"))
    request_token: Mapped[str] = mapped_column(String(96), nullable=False)
    source: Mapped[str] = mapped_column(String(32), default=PROFILE_REQUEST_SOURCE, nullable=False)
    variant_id: Mapped[str] = mapped_column(String(48), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None


def build_profile_request(last_variant_id: str | None = None) -> tuple[str, str]:
    variants = [
        (f"intro-{intro_index + 1}-price-{price_index + 1}", f"{intro}\n{price}")
        for intro_index, intro in enumerate(INTRO_VARIANTS)
        for price_index, price in enumerate(PRICE_VARIANTS)
    ]
    candidates = [item for item in variants if item[0] != last_variant_id] or variants
    return secrets.choice(candidates)


class ProfileRequestService:
    """One-tap profile request orchestration on top of canonical messaging."""

    def __init__(self, messaging) -> None:
        self.messaging = messaging
        self.database = messaging.database
        self._locks_guard = threading.Lock()
        self._locks: dict[int, threading.Lock] = {}

    def _lock_for(self, person_id: int) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(int(person_id), threading.Lock())

    @staticmethod
    def _latest_sent(db, person_id: int) -> ProfileRequest | None:
        return db.scalar(
            select(ProfileRequest)
            .where(ProfileRequest.person_id == int(person_id), ProfileRequest.status == "SENT")
            .order_by(desc(ProfileRequest.sent_at), desc(ProfileRequest.request_id))
            .limit(1)
        )

    @staticmethod
    def _payload(row: ProfileRequest | None) -> dict:
        if row is None or row.sent_at is None:
            return {
                "source": PROFILE_REQUEST_SOURCE,
                "can_send": True,
                "last_request_at": None,
                "cooldown_until": None,
                "last_variant_id": None,
                "thread_id": None,
                "message_id": None,
            }
        sent_at = _as_utc(row.sent_at)
        cooldown_until = sent_at + PROFILE_REQUEST_COOLDOWN
        return {
            "source": row.source,
            "can_send": utcnow() >= cooldown_until,
            "last_request_at": _iso(sent_at),
            "cooldown_until": _iso(cooldown_until),
            "last_variant_id": row.variant_id,
            "thread_id": row.thread_id,
            "message_id": row.message_id,
        }

    def status(self, person_id: int) -> dict:
        with self.database.session() as db:
            return self._payload(self._latest_sent(db, int(person_id)))

    def send(self, person_id: int, *, listing_id: int | None = None, request_token: str | None = None) -> dict:
        person_id = int(person_id)
        token = str(request_token or uuid.uuid4().hex).strip()[:96]
        if not token:
            token = uuid.uuid4().hex

        with self._lock_for(person_id):
            existing_id = None
            with self.database.session() as db:
                existing = db.scalar(select(ProfileRequest).where(ProfileRequest.request_token == token).limit(1))
                if existing is not None:
                    if existing.person_id != person_id:
                        raise ValueError("Request-Token gehört zu einer anderen Person")
                    existing_id = existing.request_id
                    if existing.status == "SENT":
                        payload = self._payload(existing)
                        payload.update({"sent": True, "idempotent": True})
                        return payload
                    if existing.status == "PENDING":
                        payload = self._payload(self._latest_sent(db, person_id))
                        payload.update({"sent": False, "pending": True, "idempotent": True})
                        return payload

                latest = self._latest_sent(db, person_id)
                if latest is not None and latest.sent_at is not None:
                    cooldown_until = _as_utc(latest.sent_at) + PROFILE_REQUEST_COOLDOWN
                    if utcnow() < cooldown_until:
                        payload = self._payload(latest)
                        payload.update({"sent": False, "cooldown": True})
                        return payload
                last_variant_id = latest.variant_id if latest is not None else None

            thread = self.messaging.ensure_person_thread(person_id, listing_id)
            thread_id = int(thread.get("thread_id") or 0)
            endpoint_id = int((thread.get("endpoint") or {}).get("endpoint_id") or 0)
            if not thread_id or not endpoint_id:
                raise ValueError("Für dieses Profil ist keine sendefähige WhatsApp-Nummer vorhanden")
            refresh_endpoint_identity(self.messaging, endpoint_id)

            variant_id, text = build_profile_request(last_variant_id)
            now = utcnow()
            with self.database.session() as db:
                attempt = db.get(ProfileRequest, int(existing_id)) if existing_id else None
                if attempt is None:
                    attempt = ProfileRequest(
                        person_id=person_id,
                        request_token=token,
                        source=PROFILE_REQUEST_SOURCE,
                        variant_id=variant_id,
                        text=text,
                        status="PENDING",
                        attempted_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(attempt)
                    db.flush()
                    existing_id = attempt.request_id
                else:
                    attempt.thread_id = None
                    attempt.message_id = None
                    attempt.variant_id = variant_id
                    attempt.text = text
                    attempt.status = "PENDING"
                    attempt.attempted_at = now
                    attempt.sent_at = None
                    attempt.error = None
                    attempt.updated_at = now
                attempt.thread_id = thread_id

            try:
                message = self.messaging.send_text(thread_id, text, endpoint_id)
            except Exception as exc:
                with self.database.session() as db:
                    attempt = db.get(ProfileRequest, int(existing_id))
                    if attempt is not None:
                        attempt.status = "FAILED"
                        attempt.error = str(exc)[:1000]
                        attempt.updated_at = utcnow()
                raise

            sent_at = utcnow()
            message_id = int(message.get("message_id") or message.get("id") or 0) or None
            with self.database.session() as db:
                attempt = db.get(ProfileRequest, int(existing_id))
                if attempt is None:
                    raise RuntimeError("Anfrage-Metadaten konnten nicht gespeichert werden")
                attempt.message_id = message_id
                attempt.status = "SENT"
                attempt.sent_at = sent_at
                attempt.error = None
                attempt.updated_at = sent_at

            payload = self.status(person_id)
            payload.update({"sent": True, "idempotent": False})
            return payload


def install_profile_requests(app, *, messaging) -> ProfileRequestService:
    service = ProfileRequestService(messaging)
    app.extensions["ladies_profile_requests"] = service

    def require_person(person_id: int) -> None:
        if not person_is_ladies_scoped(messaging.database, person_id):
            raise NotFoundError("Person gehört zu keiner Ladies-Anzeige")

    @app.get("/api/messaging/person/<int:person_id>/request")
    def messaging_profile_request_status(person_id: int):
        require_person(person_id)
        return jsonify(service.status(person_id))

    @app.post("/api/messaging/person/<int:person_id>/request")
    def messaging_profile_request_send(person_id: int):
        require_person(person_id)
        data = request.get_json(silent=True) or {}
        try:
            listing_id = read_int(data, "listing_id", minimum=1) if data.get("listing_id") else None
            request_token = str(data.get("request_token") or uuid.uuid4().hex).strip()[:96]
            return jsonify(service.send(person_id, listing_id=listing_id, request_token=request_token))
        except KeyError as exc:
            raise NotFoundError("Person oder Chat nicht gefunden") from exc
        except (ValueError, RuntimeError) as exc:
            raise ValidationError(str(exc)) from exc

    return service
