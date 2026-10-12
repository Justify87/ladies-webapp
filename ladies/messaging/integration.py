from __future__ import annotations

import logging

from sqlalchemy import delete, event as sa_event, select, update
from sqlalchemy.orm import Session

from ladies_v9.models import IdentityEvent, Person
from .models import ChatMessage, ChatThread, ContactEndpoint

LOGGER = logging.getLogger("ladies_explorer.messaging.identity")
MERGE_ACTIONS = {"MANUAL_MERGE", "AUTO_MATCH"}
_listener_installed = False


def _merge_messaging_in_session(db: Session, source_id: int, target_id: int) -> bool:
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


def _before_flush_identity_merge(session: Session, _flush_context, _instances) -> None:
    """Follow repository-owned identity merges before source Person is deleted.

    IdentityEvent.previous_person_id uses ON DELETE SET NULL in the existing V9
    schema. This hook therefore consumes merge events while their source id still
    exists and keeps messaging in the same transaction as the canonical merge.
    MANUAL_SPLIT is intentionally ignored: a listing split must not move a whole
    person chat.
    """
    for item in list(session.new):
        if not isinstance(item, IdentityEvent):
            continue
        if item.action not in MERGE_ACTIONS or getattr(item, "_messaging_reconciled", False):
            continue
        source_id = item.previous_person_id
        target_id = item.new_person_id
        if source_id and target_id:
            _merge_messaging_in_session(session, int(source_id), int(target_id))
        item._messaging_reconciled = True


def install_identity_merge_listener() -> None:
    global _listener_installed
    if _listener_installed:
        return
    sa_event.listen(Session, "before_flush", _before_flush_identity_merge)
    _listener_installed = True


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
            if _merge_messaging_in_session(db, int(source_id), int(target_id)):
                merged += 1
    return merged
