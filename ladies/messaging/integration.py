from __future__ import annotations

import logging

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ladies_v9.models import IdentityEvent, Person
from .models import ChatMessage, ChatThread, ContactEndpoint

LOGGER = logging.getLogger("ladies_explorer.messaging.identity")
MERGE_ACTIONS = {"MANUAL_MERGE", "AUTO_MATCH"}


def merge_messaging_person(db: Session, source_id: int, target_id: int) -> bool:
    """Move messaging data during a canonical person merge.

    The caller owns the transaction and must invoke this before the source person
    is deleted. Pricing is intentionally orchestrated before this handler because
    this function may delete a duplicate source chat thread.
    """
    if not source_id or not target_id or source_id == target_id:
        return False
    endpoints = db.scalars(select(ContactEndpoint).where(ContactEndpoint.person_id == source_id)).all()
    source_thread = db.scalar(select(ChatThread).where(ChatThread.person_id == source_id))
    if not endpoints and source_thread is None:
        return False
    for endpoint in endpoints:
        endpoint.person_id = target_id
    target_thread = db.scalar(select(ChatThread).where(ChatThread.person_id == target_id))
    if source_thread is not None:
        if target_thread is None:
            source_thread.person_id = target_id
        elif target_thread.thread_id != source_thread.thread_id:
            source_thread_id = source_thread.thread_id
            target_thread_id = target_thread.thread_id
            # New messages have not reached SQLite yet, so move those ORM objects
            # explicitly. Existing rows are moved with SQL before the source thread
            # is deleted. Avoid ORM delete-orphan cascade here: it can otherwise
            # delete messages whose FK has just been reassigned in the same flush.
            for pending in list(db.new):
                if isinstance(pending, ChatMessage) and pending.thread_id == source_thread_id:
                    pending.thread_id = target_thread_id
            db.execute(
                update(ChatMessage)
                .where(ChatMessage.thread_id == source_thread_id)
                .values(thread_id=target_thread_id)
            )
            target_thread.unread_count += source_thread.unread_count
            times = [x for x in (target_thread.last_message_at, source_thread.last_message_at) if x]
            if times:
                target_thread.last_message_at = max(times)
            db.execute(delete(ChatThread).where(ChatThread.thread_id == source_thread_id))
            try:
                db.expunge(source_thread)
            except Exception:
                pass
    LOGGER.info("Messaging person merge source_person_id=%s target_person_id=%s", source_id, target_id)
    return True


def install_identity_merge_listener() -> None:
    """Retained for the current MessagingService constructor; no global hook is installed.

    Merge integrations are configured explicitly on PersonRepository by app.py.
    """
    return None


def reconcile_identity_events(database) -> int:
    """Repair messaging refs for older/out-of-band merge events when possible."""
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
            if merge_messaging_person(db, int(source_id), int(target_id)):
                merged += 1
    return merged


__all__ = ["install_identity_merge_listener", "merge_messaging_person", "reconcile_identity_events"]
