from __future__ import annotations

import logging
from sqlalchemy import desc, select

from ladies_v9.models import IdentityEvent, Person
from .models import ChatMessage, ChatThread, ContactEndpoint

LOGGER = logging.getLogger("ladies_explorer.messaging.identity")


def reconcile_identity_events(database) -> int:
    """Make messaging follow the canonical Ladies person graph after merges.

    Messaging person ids intentionally do not drive identity. IdentityEvent is the
    bridge from the existing V9 identity subsystem into messaging.
    """
    merged = 0
    with database.session() as db:
        events = db.scalars(
            select(IdentityEvent)
            .where(IdentityEvent.previous_person_id.is_not(None))
            .order_by(IdentityEvent.identity_event_id)
        ).all()
        for event in events:
            source_id = event.previous_person_id
            target_id = event.new_person_id
            if not source_id or source_id == target_id:
                continue
            # Only replay an event if messaging still references its old person.
            has_endpoint = db.scalar(select(ContactEndpoint.endpoint_id).where(ContactEndpoint.person_id == source_id).limit(1))
            source_thread = db.scalar(select(ChatThread).where(ChatThread.person_id == source_id))
            if not has_endpoint and source_thread is None:
                continue
            if db.get(Person, target_id) is None:
                continue
            for endpoint in db.scalars(select(ContactEndpoint).where(ContactEndpoint.person_id == source_id)).all():
                endpoint.person_id = target_id
            target_thread = db.scalar(select(ChatThread).where(ChatThread.person_id == target_id))
            if source_thread is not None:
                if target_thread is None:
                    source_thread.person_id = target_id
                elif target_thread.thread_id != source_thread.thread_id:
                    for message in db.scalars(select(ChatMessage).where(ChatMessage.thread_id == source_thread.thread_id)).all():
                        message.thread_id = target_thread.thread_id
                    target_thread.unread_count += source_thread.unread_count
                    times = [x for x in (target_thread.last_message_at, source_thread.last_message_at) if x]
                    if times:
                        target_thread.last_message_at = max(times)
                    db.delete(source_thread)
            merged += 1
            LOGGER.info("Messaging identity reconciled source_person_id=%s target_person_id=%s", source_id, target_id)
    return merged
