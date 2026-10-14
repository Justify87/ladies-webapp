from __future__ import annotations

import logging

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ladies_v9.messaging.models import ChatThread
from ladies_v9.models import IdentityEvent, Person
from .models import PriceExtraction

LOGGER = logging.getLogger("ladies_explorer.pricing.identity")
MERGE_ACTIONS = {"MANUAL_MERGE", "AUTO_MATCH"}


def merge_pricing_person(db: Session, source_id: int, target_id: int) -> bool:
    """Move pricing extraction references during a canonical person merge.

    This handler must run before the messaging merge handler because messaging may
    delete a duplicate source thread that pricing still needs to identify.
    """
    if not source_id or not target_id or source_id == target_id:
        return False

    source_thread = db.scalar(select(ChatThread).where(ChatThread.person_id == source_id))
    target_thread = db.scalar(select(ChatThread).where(ChatThread.person_id == target_id))

    values = {"person_id": target_id}
    if source_thread is not None and target_thread is not None and source_thread.thread_id != target_thread.thread_id:
        values["thread_id"] = target_thread.thread_id

    predicates = [PriceExtraction.person_id == source_id]
    if source_thread is not None:
        predicates.append(PriceExtraction.thread_id == source_thread.thread_id)

    result = db.execute(update(PriceExtraction).where(or_(*predicates)).values(**values))
    moved = bool(result.rowcount)
    if moved:
        LOGGER.info("Pricing person merge source_person_id=%s target_person_id=%s", source_id, target_id)
    return moved


def reconcile_identity_events(database) -> int:
    """Repair pricing refs for persisted merge events before messaging reconciliation."""
    merged = 0
    with database.session() as db:
        events = db.scalars(
            select(IdentityEvent)
            .where(
                IdentityEvent.previous_person_id.is_not(None),
                IdentityEvent.action.in_(MERGE_ACTIONS),
            )
            .order_by(IdentityEvent.identity_event_id)
        ).all()
        for item in events:
            source_id = item.previous_person_id
            target_id = item.new_person_id
            if not source_id or source_id == target_id:
                continue
            if db.get(Person, target_id) is None:
                continue
            if merge_pricing_person(db, int(source_id), int(target_id)):
                merged += 1
    return merged


__all__ = ["merge_pricing_person", "reconcile_identity_events"]
