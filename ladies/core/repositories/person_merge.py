from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

import orjson
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ladies_v9.models import (
    ChangeEvent,
    Encounter,
    IdentityEvidence,
    IdentityEvent,
    Listing,
    Person,
    PersonIdentifier,
    PersonUserState,
    PriceList,
    SourceProfile,
    utcnow,
)

MergeHandler = Callable[[Session, int, int], Any]


def _dumps(value: Any) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode("utf-8")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def merge_persons_in_session(
    db: Session,
    *,
    source_person_id: int,
    target_person_id: int,
    action: str,
    before_merge_handlers: Iterable[MergeHandler] = (),
) -> bool:
    """Merge one person into another inside the caller-owned transaction.

    Integration handlers run in their explicit configured order while both source
    and target still exist. This is required for integrations that must migrate
    references before a source chat/person row is deleted.
    """
    source = db.get(Person, source_person_id)
    target = db.get(Person, target_person_id)
    if source is None or target is None or source_person_id == target_person_id:
        return False

    for handler in before_merge_handlers:
        handler(db, source_person_id, target_person_id)

    source_state = db.get(PersonUserState, source_person_id)
    target_state = db.get(PersonUserState, target_person_id)
    if target_state is None:
        target_state = PersonUserState(person_id=target_person_id)
        db.add(target_state)
        db.flush()
    if source_state:
        if target_state.preference == "NONE" and source_state.preference != "NONE":
            target_state.preference = source_state.preference
        target_state.favorite = target_state.favorite or source_state.favorite
        if (
            target_state.repeat_interest == "UNKNOWN"
            and source_state.repeat_interest != "UNKNOWN"
        ):
            target_state.repeat_interest = source_state.repeat_interest
        if source_state.private_note:
            target_state.private_note = (
                (target_state.private_note or "")
                + ("\n\n" if target_state.private_note else "")
                + source_state.private_note
            )[:10000]

    listings = db.scalars(
        select(Listing).where(Listing.person_id == source_person_id)
    ).all()
    for listing in listings:
        db.add(IdentityEvent(
            listing_id=listing.listing_id,
            previous_person_id=source_person_id,
            new_person_id=target_person_id,
            action=action,
            evidence_json=_dumps({"reason": listing.match_reason}),
        ))
        listing.person_id = target_person_id

    for listing in db.scalars(
        select(Listing).where(Listing.candidate_person_id == source_person_id)
    ).all():
        listing.candidate_person_id = target_person_id

    for row in db.scalars(
        select(SourceProfile).where(SourceProfile.person_id == source_person_id)
    ).all():
        duplicate = None
        if row.external_profile_id:
            duplicate = db.scalar(select(SourceProfile).where(
                SourceProfile.person_id == target_person_id,
                SourceProfile.source == row.source,
                SourceProfile.external_profile_id == row.external_profile_id,
            ))
        if duplicate:
            for listing in db.scalars(
                select(Listing).where(Listing.source_profile_id == row.source_profile_id)
            ).all():
                listing.source_profile_id = duplicate.source_profile_id
            db.delete(row)
        else:
            row.person_id = target_person_id

    for row in db.scalars(
        select(PersonIdentifier).where(PersonIdentifier.person_id == source_person_id)
    ).all():
        duplicate = db.scalar(select(PersonIdentifier).where(
            PersonIdentifier.person_id == target_person_id,
            PersonIdentifier.kind == row.kind,
            PersonIdentifier.normalized_value == row.normalized_value,
        ))
        if duplicate:
            duplicate.last_seen_at = max(
                _as_utc(duplicate.last_seen_at),
                _as_utc(row.last_seen_at),
            )
            db.delete(row)
        else:
            row.person_id = target_person_id

    for model in (PriceList, Encounter, ChangeEvent, IdentityEvidence):
        db.execute(
            update(model)
            .where(model.person_id == source_person_id)
            .values(person_id=target_person_id)
        )

    if source_state:
        db.delete(source_state)
    target.first_seen_at = min(
        _as_utc(target.first_seen_at),
        _as_utc(source.first_seen_at),
    )
    target.last_seen_at = max(
        _as_utc(target.last_seen_at),
        _as_utc(source.last_seen_at),
    )
    target.updated_at = utcnow()
    if not target.display_name and source.display_name:
        target.display_name = source.display_name
    db.delete(source)
    db.flush()
    return True
