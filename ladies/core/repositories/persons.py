from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import orjson
from sqlalchemy import desc, or_, select

from ladies_v9.database import Database
from ladies_v9.models import (
    ChangeEvent,
    Encounter,
    EncounterService,
    Listing,
    ListingObservation,
    Person,
    PersonUserState,
    PriceItem,
    PriceList,
    utcnow,
)
from ladies_v9.utils import parse_iso_datetime


def _dumps(value: Any) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode("utf-8")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class PersonRepository:
    """Person/listing persistence boundary.

    Identity/profile ingestion and the large person read model still delegate to
    the legacy store during the final migration slice. State, status, prices and
    encounters are already owned directly here.
    """

    def __init__(self, database: Database, profile_store):
        self.database = database
        self._profile_store = profile_store

    def listing_target(self, advert_id):
        with self.database.session() as db:
            listing = db.scalar(
                select(Listing).where(Listing.external_listing_id == str(advert_id))
            )
            if listing is None:
                return None
            return {
                "listing_id": listing.listing_id,
                "advert_id": listing.external_listing_id,
                "person_id": listing.person_id,
                "current_url": listing.current_url,
                "category": listing.category,
                "current_status": listing.current_status,
            }

    def ingest_profile(self, **kwargs):
        return self._profile_store.ingest_profile(**kwargs)

    def update_listing_status(
        self,
        external_listing_id: str,
        *,
        status: str,
        http_status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = str(status).upper()
        if status not in {"ACTIVE", "UNAVAILABLE", "UNKNOWN"}:
            raise ValueError("Ungültiger Status")
        now = utcnow()
        with self.database.session() as db:
            listing = db.scalar(
                select(Listing).where(Listing.external_listing_id == str(external_listing_id))
            )
            if listing is None:
                raise KeyError(external_listing_id)
            previous = listing.current_status
            listing.current_status = status
            listing.status_checked_at = now
            if status == "ACTIVE":
                listing.last_seen_at = now
                listing.ended_at = None
            elif status == "UNAVAILABLE" and listing.ended_at is None:
                listing.ended_at = now
            db.add(ListingObservation(
                listing_id=listing.listing_id,
                observed_at=now,
                source="STATUS",
                status=status,
                http_status=http_status,
                details_json=_dumps(details or {}),
            ))
            if previous != status:
                db.add(ChangeEvent(
                    person_id=listing.person_id,
                    listing_id=listing.listing_id,
                    event_type="STATUS_CHANGED",
                    field_key="status",
                    old_json=_dumps(previous),
                    new_json=_dumps(status),
                    importance="HIGH",
                ))
            return {
                "advert_id": external_listing_id,
                "status": status,
                "previous": previous,
                "person_id": listing.person_id,
            }

    def listing_detail(self, advert_id):
        return self._profile_store.listing_detail(advert_id)

    def person_detail(self, person_id):
        return self._profile_store.person_detail(person_id)

    def set_user_state(self, person_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        data = payload or {}
        with self.database.session() as db:
            person = db.get(Person, int(person_id))
            if person is None:
                raise KeyError(person_id)
            state = db.get(PersonUserState, person.person_id)
            if state is None:
                state = PersonUserState(person_id=person.person_id)
                db.add(state)
            if "preference" in data:
                preference = str(data.get("preference") or "NONE").upper()
                if preference not in {"NONE", "LIKE", "DISLIKE"}:
                    raise ValueError("Ungültige preference")
                state.preference = preference
            if "favorite" in data:
                state.favorite = bool(data.get("favorite"))
            if "repeat_interest" in data:
                state.repeat_interest = str(data.get("repeat_interest") or "UNKNOWN")[:32]
            if "private_note" in data:
                state.private_note = str(data.get("private_note") or "")[:10000] or None
            state.updated_at = utcnow()
            db.flush()
            return self._state_payload(state)

    def add_price_list(self, person_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        data = payload or {}
        items = list(data.get("items") or [])
        if not items:
            raise ValueError("Mindestens ein Preiseintrag ist erforderlich")
        with self.database.session() as db:
            if db.get(Person, int(person_id)) is None:
                raise KeyError(person_id)
            observed = parse_iso_datetime(data.get("observed_at")) or utcnow()
            listing_id = data.get("listing_id")
            row = PriceList(
                person_id=int(person_id),
                listing_id=int(listing_id) if listing_id else None,
                observed_at=observed,
                source=str(data.get("source") or "USER_ENTERED")[:32],
                currency="EUR",
                note=str(data.get("note") or "")[:4000] or None,
            )
            db.add(row)
            db.flush()
            for item in items:
                amount = item.get("amount_cents")
                if amount is None and item.get("amount_eur") is not None:
                    amount = round(float(item.get("amount_eur")) * 100)
                amount = int(amount or 0)
                if amount < 0:
                    raise ValueError("Preis darf nicht negativ sein")
                db.add(PriceItem(
                    price_list_id=row.price_list_id,
                    item_type=str(item.get("item_type") or "BASE")[:24],
                    label=str(item.get("label") or "")[:256] or None,
                    duration_minutes=(
                        int(item["duration_minutes"])
                        if item.get("duration_minutes") not in {None, ""} else None
                    ),
                    service=str(item.get("service") or "")[:256] or None,
                    amount_cents=amount,
                    note=str(item.get("note") or "")[:2000] or None,
                ))
            db.flush()
            return self._price_list_payload(db, row)

    def add_encounter(self, person_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        data = payload or {}
        with self.database.session() as db:
            if db.get(Person, int(person_id)) is None:
                raise KeyError(person_id)
            occurred = parse_iso_datetime(data.get("occurred_at")) or utcnow()

            def cents(name: str) -> int | None:
                value = data.get(name)
                if value in {None, ""}:
                    return None
                return round(float(value) * 100) if name.endswith("_eur") else int(value)

            row = Encounter(
                person_id=int(person_id),
                listing_id=int(data["listing_id"]) if data.get("listing_id") else None,
                occurred_at=occurred,
                duration_minutes=(
                    int(data["duration_minutes"])
                    if data.get("duration_minutes") not in {None, ""} else None
                ),
                base_price_cents=cents("base_price_eur"),
                total_price_cents=cents("total_price_eur"),
                overall_rating=int(data["overall_rating"]) if data.get("overall_rating") else None,
                sympathy_rating=int(data["sympathy_rating"]) if data.get("sympathy_rating") else None,
                photo_status=str(data.get("photo_status") or "UNKNOWN")[:24],
                service_accuracy=str(data.get("service_accuracy") or "UNKNOWN")[:24],
                hygiene=str(data.get("hygiene") or "UNKNOWN")[:24],
                kissing=str(data.get("kissing") or "UNKNOWN")[:24],
                negotiable=str(data.get("negotiable") or "UNKNOWN")[:24],
                note=str(data.get("note") or "")[:10000] or None,
            )
            db.add(row)
            db.flush()
            for item in list(data.get("services") or []):
                db.add(EncounterService(
                    encounter_id=row.encounter_id,
                    service_name=str(item.get("service_name") or item.get("name") or "")[:256],
                    amount_cents=(
                        round(float(item.get("amount_eur")) * 100)
                        if item.get("amount_eur") not in {None, ""} else None
                    ),
                    performed=bool(item.get("performed", True)),
                    note=str(item.get("note") or "")[:2000] or None,
                ))
            db.flush()
            return self._encounter_payload(db, row)

    def update_encounter(
        self,
        person_id: int,
        encounter_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        data = payload or {}

        def cents(key: str) -> int | None:
            value = data.get(key)
            return round(float(value) * 100) if value not in {None, ""} else None

        with self.database.session() as db:
            row = db.get(Encounter, int(encounter_id))
            if row is None or row.person_id != int(person_id):
                raise KeyError(encounter_id)
            if "listing_id" in data:
                row.listing_id = int(data["listing_id"]) if data.get("listing_id") else None
            if "occurred_at" in data:
                row.occurred_at = parse_iso_datetime(data.get("occurred_at")) or row.occurred_at
            if "duration_minutes" in data:
                row.duration_minutes = (
                    int(data["duration_minutes"])
                    if data.get("duration_minutes") not in {None, ""} else None
                )
            if "base_price_eur" in data:
                row.base_price_cents = cents("base_price_eur")
            if "total_price_eur" in data:
                row.total_price_cents = cents("total_price_eur")
            if "overall_rating" in data:
                row.overall_rating = int(data["overall_rating"]) if data.get("overall_rating") else None
            if "sympathy_rating" in data:
                row.sympathy_rating = int(data["sympathy_rating"]) if data.get("sympathy_rating") else None
            for field in ("photo_status", "service_accuracy", "hygiene", "kissing", "negotiable"):
                if field in data:
                    setattr(row, field, str(data.get(field) or "UNKNOWN")[:24])
            if "note" in data:
                row.note = str(data.get("note") or "")[:10000] or None
            row.updated_at = utcnow()

            if "services" in data:
                for service in list(row.services):
                    db.delete(service)
                for item in list(data.get("services") or []):
                    name = str(item.get("service_name") or item.get("name") or "")[:256]
                    if not name:
                        continue
                    db.add(EncounterService(
                        encounter_id=row.encounter_id,
                        service_name=name,
                        amount_cents=(
                            round(float(item.get("amount_eur")) * 100)
                            if item.get("amount_eur") not in {None, ""} else None
                        ),
                        performed=bool(item.get("performed", True)),
                        note=str(item.get("note") or "")[:2000] or None,
                    ))
            db.flush()
            return self._encounter_payload(db, row)

    def lists(self, tab):
        return self._profile_store.lists(tab)

    def status_targets(self) -> list[dict[str, Any]]:
        with self.database.session() as db:
            rows = db.scalars(
                select(Listing)
                .join(PersonUserState, PersonUserState.person_id == Listing.person_id)
                .where(or_(
                    PersonUserState.preference != "NONE",
                    PersonUserState.favorite.is_(True),
                ))
                .order_by(desc(Listing.last_seen_at))
            ).all()
            return [
                {
                    "advert_id": row.external_listing_id,
                    "profile_url": row.current_url,
                    "person_id": row.person_id,
                    "status": row.current_status,
                }
                for row in rows
            ]

    def merge_persons(self, source_person_id, target_person_id):
        return self._profile_store.merge_persons(source_person_id, target_person_id)

    def split_listing(self, listing_id):
        return self._profile_store.split_listing(listing_id)

    @staticmethod
    def _state_payload(state: PersonUserState | None) -> dict[str, Any]:
        return {
            "preference": state.preference if state else "NONE",
            "favorite": bool(state.favorite) if state else False,
            "repeat_interest": state.repeat_interest if state else "UNKNOWN",
            "private_note": state.private_note if state else "",
            "updated_at": _iso(state.updated_at) if state else None,
        }

    @staticmethod
    def _price_list_payload(db, row: PriceList) -> dict[str, Any]:
        items = db.scalars(
            select(PriceItem)
            .where(PriceItem.price_list_id == row.price_list_id)
            .order_by(PriceItem.price_item_id)
        ).all()
        return {
            "price_list_id": row.price_list_id,
            "person_id": row.person_id,
            "listing_id": row.listing_id,
            "observed_at": _iso(row.observed_at),
            "source": row.source,
            "currency": row.currency,
            "note": row.note,
            "items": [
                {
                    "price_item_id": item.price_item_id,
                    "item_type": item.item_type,
                    "label": item.label,
                    "duration_minutes": item.duration_minutes,
                    "service": item.service,
                    "amount_cents": item.amount_cents,
                    "amount_eur": item.amount_cents / 100,
                    "note": item.note,
                }
                for item in items
            ],
        }

    @staticmethod
    def _encounter_payload(db, row: Encounter) -> dict[str, Any]:
        items = db.scalars(
            select(EncounterService)
            .where(EncounterService.encounter_id == row.encounter_id)
            .order_by(EncounterService.encounter_service_id)
        ).all()
        return {
            "encounter_id": row.encounter_id,
            "person_id": row.person_id,
            "listing_id": row.listing_id,
            "occurred_at": _iso(row.occurred_at),
            "duration_minutes": row.duration_minutes,
            "base_price_eur": row.base_price_cents / 100 if row.base_price_cents is not None else None,
            "total_price_eur": row.total_price_cents / 100 if row.total_price_cents is not None else None,
            "overall_rating": row.overall_rating,
            "sympathy_rating": row.sympathy_rating,
            "photo_status": row.photo_status,
            "service_accuracy": row.service_accuracy,
            "hygiene": row.hygiene,
            "kissing": row.kissing,
            "negotiable": row.negotiable,
            "note": row.note,
            "services": [
                {
                    "service_name": item.service_name,
                    "amount_eur": item.amount_cents / 100 if item.amount_cents is not None else None,
                    "performed": item.performed,
                    "note": item.note,
                }
                for item in items
            ],
        }
