from __future__ import annotations

import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone

from flask import jsonify, request
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, desc, or_, select
from sqlalchemy.orm import Mapped, mapped_column

from ladies_v9.errors import NotFoundError, ValidationError
from ladies_v9.models import Base, Listing, utcnow
from ladies_v9.request_parsing import read_int

from .models import ChatMessage, ChatThread, ContactEndpoint
from .scope import person_is_ladies_scoped, refresh_endpoint_identity

PROFILE_REQUEST_SOURCE = "profile_request"
PROFILE_REQUEST_COOLDOWN = timedelta(hours=24)
PROFILE_REQUEST_STALE_AFTER = timedelta(minutes=1)
SUCCESS_MESSAGE_STATUSES = {"SENT", "DELIVERED", "READ", "PLAYED"}
UNRESOLVED_REQUEST_STATUSES = {"PREPARED", "PENDING", "UNCERTAIN"}

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
    # Person identity can be merged. The canonical merge integration moves this
    # reference explicitly before the source person is deleted.
    person_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    thread_id: Mapped[int | None] = mapped_column(ForeignKey("chat_threads.thread_id", ondelete="SET NULL"))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.message_id", ondelete="SET NULL"))
    request_token: Mapped[str] = mapped_column(String(96), nullable=False)
    source: Mapped[str] = mapped_column(String(32), default=PROFILE_REQUEST_SOURCE, nullable=False)
    variant_id: Mapped[str] = mapped_column(String(48), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="PREPARED", nullable=False)
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


def _definitely_not_sent(exc: Exception) -> bool:
    if isinstance(exc, (KeyError, ValueError)):
        return True
    return isinstance(exc, RuntimeError) and str(exc).strip() == "WhatsApp ist derzeit nicht verfügbar"


class ProfileRequestService:
    """One-tap request orchestration on top of the canonical messaging dispatcher."""

    def __init__(self, messaging) -> None:
        self.messaging = messaging
        self.database = messaging.database
        self._locks_guard = threading.Lock()
        self._locks: dict[int, threading.Lock] = {}

    def _lock_for(self, person_id: int) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(int(person_id), threading.Lock())

    @staticmethod
    def _latest(db, person_id: int) -> ProfileRequest | None:
        return db.scalar(
            select(ProfileRequest)
            .where(ProfileRequest.person_id == int(person_id))
            .order_by(desc(ProfileRequest.attempted_at), desc(ProfileRequest.request_id))
            .limit(1)
        )

    @staticmethod
    def _latest_sent(db, person_id: int) -> ProfileRequest | None:
        return db.scalar(
            select(ProfileRequest)
            .where(ProfileRequest.person_id == int(person_id), ProfileRequest.status == "SENT")
            .order_by(desc(ProfileRequest.sent_at), desc(ProfileRequest.request_id))
            .limit(1)
        )

    @staticmethod
    def _confirmed_message(db, row: ProfileRequest) -> ChatMessage | None:
        linked = db.get(ChatMessage, int(row.message_id)) if row.message_id else None
        if linked is not None and (
            linked.provider_message_id is not None or linked.status in SUCCESS_MESSAGE_STATUSES
        ):
            return linked
        if not row.thread_id:
            return None
        query = select(ChatMessage).where(
            ChatMessage.thread_id == int(row.thread_id),
            ChatMessage.direction == "OUT",
            ChatMessage.message_type == "TEXT",
            ChatMessage.text == row.text,
            ChatMessage.timestamp >= row.attempted_at,
            or_(
                ChatMessage.provider_message_id.is_not(None),
                ChatMessage.status.in_(SUCCESS_MESSAGE_STATUSES),
            ),
        )
        if row.message_id:
            query = query.where(ChatMessage.message_id != int(row.message_id))
        return db.scalar(query.order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id)).limit(1))

    @staticmethod
    def _mark_confirmed(row: ProfileRequest, message: ChatMessage, now: datetime) -> None:
        row.message_id = int(message.message_id)
        row.thread_id = int(message.thread_id)
        row.status = "SENT"
        row.sent_at = _as_utc(message.timestamp) if message.timestamp else now
        row.error = None
        row.updated_at = now

    def _reconcile_person(self, person_id: int) -> None:
        now = utcnow()
        with self.database.session() as db:
            rows = db.scalars(
                select(ProfileRequest)
                .where(
                    ProfileRequest.person_id == int(person_id),
                    ProfileRequest.status.in_(UNRESOLVED_REQUEST_STATUSES),
                )
                .order_by(ProfileRequest.request_id)
            ).all()
            for row in rows:
                confirmed = self._confirmed_message(db, row)
                if confirmed is not None:
                    self._mark_confirmed(row, confirmed, now)
                    continue
                stale_at = _as_utc(row.attempted_at) + PROFILE_REQUEST_STALE_AFTER
                if now < stale_at:
                    continue
                message = db.get(ChatMessage, int(row.message_id)) if row.message_id else None
                if row.status == "PREPARED":
                    # PREPARED is committed before the provider boundary. If the
                    # process dies here, no WhatsApp send was started and retry is safe.
                    row.status = "FAILED"
                    row.error = "Versand wurde vor der Übergabe an WhatsApp unterbrochen"
                    row.updated_at = now
                    if message is not None and message.status == "PENDING":
                        message.status = "FAILED"
                        message.error = row.error
                    continue
                if row.status == "PENDING":
                    # Once dispatch started, absence of a response is ambiguous.
                    # Never convert this into a retryable failure automatically.
                    row.status = "UNCERTAIN"
                    row.error = "WhatsApp-Versandstatus konnte nicht bestätigt werden"
                    row.updated_at = now
                    if message is not None and message.status == "FAILED":
                        message.status = "PENDING"
                        message.error = row.error

    @staticmethod
    def _payload_for_rows(latest: ProfileRequest | None, latest_sent: ProfileRequest | None) -> dict:
        now = utcnow()
        last_sent_at = _as_utc(latest_sent.sent_at) if latest_sent and latest_sent.sent_at else None
        successful_cooldown = last_sent_at + PROFILE_REQUEST_COOLDOWN if last_sent_at else None
        can_send = successful_cooldown is None or now >= successful_cooldown
        cooldown_until = successful_cooldown if not can_send else None
        refresh_at = cooldown_until
        pending = False
        uncertain = False
        retryable = False

        if latest is not None and latest.status == "PREPARED":
            prepared_until = _as_utc(latest.attempted_at) + PROFILE_REQUEST_STALE_AFTER
            if now < prepared_until:
                can_send = False
                cooldown_until = prepared_until
                refresh_at = prepared_until
                pending = True
        elif latest is not None and latest.status == "PENDING":
            protection_until = _as_utc(latest.attempted_at) + PROFILE_REQUEST_COOLDOWN
            if now < protection_until:
                can_send = False
                cooldown_until = protection_until
                refresh_at = min(
                    _as_utc(latest.attempted_at) + PROFILE_REQUEST_STALE_AFTER,
                    protection_until,
                )
                if refresh_at <= now:
                    refresh_at = protection_until
                pending = True
        elif latest is not None and latest.status == "UNCERTAIN":
            protection_until = _as_utc(latest.attempted_at) + PROFILE_REQUEST_COOLDOWN
            if now < protection_until:
                can_send = False
                cooldown_until = protection_until
                refresh_at = protection_until
                uncertain = True
        elif latest is not None and latest.status == "FAILED" and can_send:
            retryable = True

        active = latest or latest_sent
        return {
            "source": PROFILE_REQUEST_SOURCE,
            "can_send": bool(can_send),
            "last_request_at": _iso(last_sent_at),
            "cooldown_until": _iso(cooldown_until),
            "refresh_at": _iso(refresh_at),
            "last_variant_id": latest_sent.variant_id if latest_sent else None,
            "thread_id": active.thread_id if active else None,
            "message_id": active.message_id if active else None,
            "attempt_status": latest.status if latest else None,
            "attempted_at": _iso(latest.attempted_at) if latest else None,
            "pending": pending,
            "uncertain": uncertain,
            "retryable": retryable,
            "last_error": latest.error if latest and latest.status in {"FAILED", "UNCERTAIN"} else None,
        }

    def _payload(self, db, person_id: int) -> dict:
        return self._payload_for_rows(self._latest(db, person_id), self._latest_sent(db, person_id))

    def status(self, person_id: int) -> dict:
        person_id = int(person_id)
        self._reconcile_person(person_id)
        with self.database.session() as db:
            return self._payload(db, person_id)

    def _validate_listing(self, person_id: int, listing_id: int | None) -> None:
        if listing_id is None:
            return
        with self.database.session() as db:
            listing = db.get(Listing, int(listing_id))
            if listing is None or int(listing.person_id) != int(person_id):
                raise ValueError("Die ausgewählte Anzeige gehört nicht zu dieser Person")

    def _prepare_new_attempt(
        self,
        person_id: int,
        *,
        thread_id: int,
        endpoint_id: int,
        token: str,
        variant_id: str,
        text: str,
    ) -> tuple[int, int]:
        now = utcnow()
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            endpoint = db.get(ContactEndpoint, int(endpoint_id))
            if (
                thread is None
                or int(thread.person_id or 0) != int(person_id)
                or endpoint is None
                or int(endpoint.person_id or 0) != int(person_id)
                or endpoint.state != "ACTIVE"
            ):
                raise ValueError("Kein gültiger WhatsApp-Endpunkt ausgewählt")

            # Recheck inside the transaction after ensure_person_thread so a
            # concurrent request cannot bypass the central business rule.
            latest_sent = self._latest_sent(db, person_id)
            if latest_sent and latest_sent.sent_at:
                if now < _as_utc(latest_sent.sent_at) + PROFILE_REQUEST_COOLDOWN:
                    raise ValueError("Für dieses Profil wurde bereits kürzlich eine Anfrage gesendet")
            latest = self._latest(db, person_id)
            if latest and latest.status in UNRESOLVED_REQUEST_STATUSES:
                protection = (
                    _as_utc(latest.attempted_at) + PROFILE_REQUEST_STALE_AFTER
                    if latest.status == "PREPARED"
                    else _as_utc(latest.attempted_at) + PROFILE_REQUEST_COOLDOWN
                )
                if now < protection:
                    raise ValueError("Eine Anfrage für dieses Profil wird bereits verarbeitet")

            message = ChatMessage(
                thread_id=thread.thread_id,
                endpoint_id=endpoint.endpoint_id,
                direction="OUT",
                message_type="TEXT",
                text=text,
                status="PENDING",
            )
            db.add(message)
            db.flush()
            attempt = ProfileRequest(
                person_id=person_id,
                thread_id=thread.thread_id,
                message_id=message.message_id,
                request_token=token,
                source=PROFILE_REQUEST_SOURCE,
                variant_id=variant_id,
                text=text,
                status="PREPARED",
                attempted_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(attempt)
            db.flush()
            return int(attempt.request_id), int(message.message_id)

    def _begin_dispatch(self, request_id: int) -> tuple[int, int]:
        now = utcnow()
        with self.database.session() as db:
            row = db.get(ProfileRequest, int(request_id))
            if row is None or not row.message_id:
                raise RuntimeError("Anfrage-Metadaten konnten nicht geladen werden")
            message = db.get(ChatMessage, int(row.message_id))
            if message is None:
                raise RuntimeError("Anfrage-Nachricht konnte nicht geladen werden")
            row.status = "PENDING"
            row.attempted_at = now
            row.error = None
            row.updated_at = now
            return int(row.message_id), int(row.thread_id or message.thread_id)

    def _finish_sent(self, request_id: int) -> None:
        now = utcnow()
        with self.database.session() as db:
            row = db.get(ProfileRequest, int(request_id))
            if row is None or not row.message_id:
                raise RuntimeError("Anfrage-Metadaten konnten nicht gespeichert werden")
            message = db.get(ChatMessage, int(row.message_id))
            if message is None:
                raise RuntimeError("Anfrage-Nachricht konnte nicht gespeichert werden")
            row.status = "SENT"
            row.sent_at = now
            row.thread_id = int(message.thread_id)
            row.error = None
            row.updated_at = now

    def _finish_error(self, request_id: int, exc: Exception) -> None:
        now = utcnow()
        error = str(exc)[:1000]
        retryable = _definitely_not_sent(exc)
        with self.database.session() as db:
            row = db.get(ProfileRequest, int(request_id))
            if row is None:
                return
            row.status = "FAILED" if retryable else "UNCERTAIN"
            row.error = error
            row.updated_at = now
            message = db.get(ChatMessage, int(row.message_id)) if row.message_id else None
            if message is None:
                return
            if retryable:
                message.status = "FAILED"
                message.error = error
            elif message.status == "FAILED":
                # Generic provider errors and timeouts can occur after WhatsApp
                # accepted the send. Keep the canonical message non-retryable.
                message.status = "PENDING"
                message.error = "WhatsApp-Versandstatus unklar; erneuter Versand ist vorerst gesperrt"

    def _retry_failed(self, request_id: int) -> dict:
        with self.database.session() as db:
            row = db.get(ProfileRequest, int(request_id))
            if row is None or not row.message_id:
                raise RuntimeError("Fehlgeschlagene Anfrage kann nicht erneut gesendet werden")
            message = db.get(ChatMessage, int(row.message_id))
            if message is None:
                raise RuntimeError("Fehlgeschlagene Anfrage-Nachricht fehlt")
            if message.provider_message_id or message.status in SUCCESS_MESSAGE_STATUSES:
                self._mark_confirmed(row, message, utcnow())
                return self._payload(db, int(row.person_id))
            if message.status != "FAILED":
                raise RuntimeError("Anfrage ist nicht sicher erneut sendbar")
            row.status = "PREPARED"
            row.error = None
            row.updated_at = utcnow()
            message_id = int(message.message_id)

        message_id, _ = self._begin_dispatch(request_id)
        try:
            self.messaging.retry(message_id)
        except Exception as exc:
            self._finish_error(request_id, exc)
            raise
        self._finish_sent(request_id)
        payload = self.status(int(self._request_person_id(request_id)))
        payload.update({"sent": True, "idempotent": False, "retried": True})
        return payload

    def _request_person_id(self, request_id: int) -> int:
        with self.database.session() as db:
            row = db.get(ProfileRequest, int(request_id))
            if row is None:
                raise KeyError(request_id)
            return int(row.person_id)

    def send(self, person_id: int, *, listing_id: int | None = None, request_token: str | None = None) -> dict:
        person_id = int(person_id)
        token = str(request_token or uuid.uuid4().hex).strip()[:96] or uuid.uuid4().hex

        with self._lock_for(person_id):
            self._reconcile_person(person_id)
            retry_id = None
            last_variant_id = None
            with self.database.session() as db:
                existing = db.scalar(select(ProfileRequest).where(ProfileRequest.request_token == token).limit(1))
                latest = self._latest(db, person_id)
                latest_sent = self._latest_sent(db, person_id)
                payload = self._payload_for_rows(latest, latest_sent)

                if existing is not None:
                    if int(existing.person_id) != person_id:
                        raise ValueError("Request-Token gehört zu einer anderen Person")
                    if existing.status == "SENT":
                        result = self._payload_for_rows(existing, existing)
                        result.update({"sent": True, "idempotent": True})
                        return result
                    if existing.status in UNRESOLVED_REQUEST_STATUSES:
                        payload.update({"sent": False, "idempotent": True})
                        return payload
                    if existing.status == "FAILED" and latest and latest.request_id == existing.request_id and payload["can_send"]:
                        retry_id = int(existing.request_id)
                    else:
                        payload.update({"sent": False, "idempotent": True})
                        return payload

                if retry_id is None and not payload["can_send"]:
                    payload.update({"sent": False, "cooldown": not payload["pending"] and not payload["uncertain"]})
                    return payload
                if retry_id is None and latest is not None and latest.status == "FAILED":
                    retry_id = int(latest.request_id)
                last_variant_id = latest_sent.variant_id if latest_sent else None

            if retry_id is not None:
                return self._retry_failed(retry_id)

            self._validate_listing(person_id, listing_id)
            thread = self.messaging.ensure_person_thread(person_id, listing_id)
            thread_id = int(thread.get("thread_id") or 0)
            endpoint_id = int((thread.get("endpoint") or {}).get("endpoint_id") or 0)
            if not thread_id or not endpoint_id:
                raise ValueError("Für dieses Profil ist keine sendefähige WhatsApp-Nummer vorhanden")
            refresh_endpoint_identity(self.messaging, endpoint_id)

            variant_id, text = build_profile_request(last_variant_id)
            request_id, message_id = self._prepare_new_attempt(
                person_id,
                thread_id=thread_id,
                endpoint_id=endpoint_id,
                token=token,
                variant_id=variant_id,
                text=text,
            )
            self.messaging.events.publish({"type": "message", "thread_id": thread_id, "message_id": message_id})
            self._begin_dispatch(request_id)
            try:
                self.messaging._dispatch_text(message_id)
            except Exception as exc:
                self._finish_error(request_id, exc)
                raise
            self._finish_sent(request_id)
            payload = self.status(person_id)
            payload.update({"sent": True, "idempotent": False, "retried": False})
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
