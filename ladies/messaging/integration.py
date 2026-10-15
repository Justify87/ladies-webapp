from __future__ import annotations

import logging

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ladies_v9.models import IdentityEvent, Person

from .classification import (
    NON_LADIES,
    endpoint_status_in_session,
    reconcile_thread_in_session,
)
from .models import ChatMessage, ChatThread, ContactEndpoint

LOGGER = logging.getLogger("ladies_explorer.messaging.identity")
MERGE_ACTIONS = {"MANUAL_MERGE", "AUTO_MATCH"}


def _move_profile_request_thread_refs(
    db: Session,
    source_thread_id: int,
    target_thread_id: int | None,
) -> bool:
    from .profile_requests import ProfileRequest

    changed = False
    for pending in list(db.new):
        if isinstance(pending, ProfileRequest) and pending.thread_id == int(source_thread_id):
            pending.thread_id = int(target_thread_id) if target_thread_id is not None else None
            changed = True
    result = db.execute(
        update(ProfileRequest)
        .where(ProfileRequest.thread_id == int(source_thread_id))
        .values(thread_id=int(target_thread_id) if target_thread_id is not None else None)
    )
    return changed or bool(result.rowcount)


def _merge_profile_request_refs(
    db: Session,
    source_id: int,
    target_id: int,
    *,
    source_thread_id: int | None = None,
    target_thread_id: int | None = None,
) -> bool:
    from .profile_requests import ProfileRequest

    changed = False
    for pending in list(db.new):
        if not isinstance(pending, ProfileRequest):
            continue
        if int(pending.person_id or 0) == int(source_id):
            pending.person_id = int(target_id)
            changed = True
    result = db.execute(
        update(ProfileRequest)
        .where(ProfileRequest.person_id == int(source_id))
        .values(person_id=int(target_id))
    )
    changed = changed or bool(result.rowcount)
    if source_thread_id is not None:
        changed = _move_profile_request_thread_refs(
            db,
            int(source_thread_id),
            int(target_thread_id) if target_thread_id is not None else None,
        ) or changed
    return changed


def _move_mergeable_source_endpoints(
    db: Session,
    endpoints: list[ContactEndpoint],
    source_id: int,
    target_id: int,
) -> bool:
    changed = False
    for endpoint in endpoints:
        if endpoint_status_in_session(db, int(endpoint.endpoint_id)) == NON_LADIES:
            if endpoint.person_id is not None:
                endpoint.person_id = None
                changed = True
            continue
        if endpoint.person_id == int(source_id):
            endpoint.person_id = int(target_id)
            changed = True
    return changed


def merge_messaging_person(db: Session, source_id: int, target_id: int) -> bool:
    """Move messaging data during a person merge without crossing product boundaries.

    NON_LADIES conversations are structurally detached before any person/thread
    merge and therefore remain private, persisted conversations. Confirmed/unknown
    messaging can then follow the canonical identity merge normally.
    """

    if not source_id or not target_id or source_id == target_id:
        return False

    from .profile_requests import ProfileRequest

    endpoints = db.scalars(select(ContactEndpoint).where(ContactEndpoint.person_id == source_id)).all()
    source_thread = db.scalar(select(ChatThread).where(ChatThread.person_id == source_id))
    target_thread = db.scalar(select(ChatThread).where(ChatThread.person_id == target_id))
    has_requests = db.scalar(
        select(ProfileRequest.request_id).where(ProfileRequest.person_id == source_id).limit(1)
    ) is not None
    if not endpoints and source_thread is None and not has_requests:
        return False

    source_status = reconcile_thread_in_session(db, source_thread).status if source_thread is not None else None
    target_status = reconcile_thread_in_session(db, target_thread).status if target_thread is not None else None

    merge_source = source_thread if source_thread is not None and source_status != NON_LADIES else None
    merge_target = target_thread if target_thread is not None and target_status != NON_LADIES else None

    changed = bool(endpoints or source_thread is not None)
    changed = _move_mergeable_source_endpoints(db, endpoints, source_id, target_id) or changed

    if target_thread is not None and target_status == NON_LADIES:
        changed = _move_profile_request_thread_refs(
            db,
            int(target_thread.thread_id),
            int(merge_source.thread_id) if merge_source is not None else None,
        ) or changed

    if source_thread is not None and source_status == NON_LADIES:
        changed = _merge_profile_request_refs(
            db,
            source_id,
            target_id,
            source_thread_id=int(source_thread.thread_id),
            target_thread_id=int(merge_target.thread_id) if merge_target is not None else None,
        ) or changed
    elif merge_source is not None:
        if merge_target is None:
            merge_source.person_id = int(target_id)
            changed = _merge_profile_request_refs(db, source_id, target_id) or changed
            merge_target = merge_source
        elif merge_target.thread_id != merge_source.thread_id:
            source_thread_id = int(merge_source.thread_id)
            target_thread_id = int(merge_target.thread_id)
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
            merge_target.unread_count += merge_source.unread_count
            times = [x for x in (merge_target.last_message_at, merge_source.last_message_at) if x]
            if times:
                merge_target.last_message_at = max(times)
            db.execute(delete(ChatThread).where(ChatThread.thread_id == source_thread_id))
            try:
                db.expunge(merge_source)
            except Exception:
                pass
        else:
            changed = _merge_profile_request_refs(db, source_id, target_id) or changed
    else:
        changed = _merge_profile_request_refs(db, source_id, target_id) or changed

    if merge_target is not None:
        # Newly moved endpoints inherit the surviving thread's sticky decision;
        # if the target person now provides durable Ladies provenance, UNKNOWN is
        # promoted exactly once here inside the same identity-merge transaction.
        reconcile_thread_in_session(db, merge_target)

    LOGGER.info(
        "Messaging person merge source_person_id=%s target_person_id=%s source_status=%s target_status=%s",
        source_id,
        target_id,
        source_status,
        target_status,
    )
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
