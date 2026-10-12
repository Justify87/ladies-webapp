from __future__ import annotations

import logging

from sqlalchemy import event as sa_event, or_, select, update
from sqlalchemy.orm import Session

from ladies_v9.models import IdentityEvent
from ladies_v9.messaging.models import ChatThread
from .models import PriceExtraction

LOGGER = logging.getLogger("ladies_explorer.pricing.identity")
MERGE_ACTIONS = {"MANUAL_MERGE", "AUTO_MATCH"}
_listener_installed = False


def _merge_pricing_in_session(db: Session, source_id: int, target_id: int) -> bool:
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


def _before_flush_identity_merge(session: Session, _flush_context, _instances) -> None:
    for item in list(session.new):
        if not isinstance(item, IdentityEvent):
            continue
        if item.action not in MERGE_ACTIONS or getattr(item, "_pricing_reconciled", False):
            continue
        source_id = item.previous_person_id
        target_id = item.new_person_id
        if source_id and target_id:
            _merge_pricing_in_session(session, int(source_id), int(target_id))
        item._pricing_reconciled = True


def install_pricing_identity_merge_listener() -> None:
    global _listener_installed
    if _listener_installed:
        return
    # Register before the messaging listener. This moves extraction.thread_id
    # before a duplicate source chat thread can be deleted by messaging merge.
    sa_event.listen(Session, "before_flush", _before_flush_identity_merge)
    _listener_installed = True


__all__ = ["install_pricing_identity_merge_listener", "_merge_pricing_in_session"]
