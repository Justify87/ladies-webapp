from __future__ import annotations

import logging

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ladies_v9.models import IdentityEvent, Person
from .models import ChatMessage, ChatThread, ContactEndpoint

LOGGER = logging.getLogger("ladies_explorer.messaging.identity")
MERGE_ACTIONS = {"MANUAL_MERGE", "AUTO_MATCH"}


def _merge_profile_request_refs(
    db: Session,
    source_id: int,
    target_id: int,
    *,
    source_thread_id: int | None = None,
    target_thread_id: int | None = None,
) -> bool:
    # Import lazily so service.py -> integration.py does not create an import cycle
    # while the messaging package registers the additive ProfileRequest model.
    from .profile_requests import ProfileRequest

    changed = False
    for pending in list(db.new):
        if not isinstance(pending, ProfileRequest):
            continue
        if int(pending.person_id or 0) == int(source_id):
            pending.person_id = int(target_id)
            changed = True
        if source_thread_id and target_thread_id and pending.thread_id == source_thread_id:
            pending.thread_id = target_thread_id
            changed = True

    result = db.execute(
        update(ProfileRequest)
        .where(ProfileRequest.person_id == int(source_id))
        .values(person_id=int(target_id))
    )
    changed = changed or bool(result.rowcount)
    if source_thread_id and target_thread_id and source_thread_id != target_thread_id:
        result = db.execute(
            update(ProfileRequest)
            .where(ProfileRequest.thread_id == int(source_thread_id))
            .values(thread_id=int(target_thread_id))
        )
        changed = changed or bool(result.rowcount)
    return changed


def merge_messaging_person(db: Session, source_id: int, target_id: int) -> bool:
    """Move canonical messaging and profile-request data during a person merge."""
    if not source_id or not target_id or source_id == target_id:
        return False

    from .profile_requests import ProfileRequest

    endpoints = db.scalars(select(ContactEndpoint).where(ContactEndpoint.person_id == source_id)).all()
    source_thread = db.scalar(select(ChatThread).where(ChatThread.person_id == source_id))
    has_requests = db.scalar(
        select(ProfileRequest.request_id).where(ProfileRequest.person_id == source_id).limit(1)
    ) is not None
    if not endpoints and source_thread is None and not has_requests:
        return False

    changed = bool(endpoints or source_thread is not None)
    for endpoint in endpoints:
        endpoint.person_id = target_id

    target_thread = db.scalar(select(ChatThread).where(ChatThread.person_id == target_id))
    if source_thread is not None:
        if target_thread is None:
            source_thread.person_id = target_id
            changed = _merge_profile_request_refs(db, source_id, target_id) or changed
        elif target_thread.thread_id != source_thread.thread_id:
            source_thread_id = int(source_thread.thread_id)
            target_thread_id = int(target_thread.thread_id)
            # Move request refs before deleting the source thread so ON DELETE
            # cannot erase the canonical request/thread relationship.
            changed = _merge_profile_request_refs(
                db,
                source_id,
                target_id,
                source_thread_id=source_thread_id,
                target_thread_id=target_thread_id,
            ) or changed
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
        else:
            changed = _merge_profile_request_refs(db, source_id, target_id) or changed
    else:
        changed = _merge_profile_request_refs(db, source_id, target_id) or changed

    LOGGER.info("Messaging person merge source_person_id=%s target_person_id=%s", source_id, target_id)
    return changed


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
