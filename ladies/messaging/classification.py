from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ladies_v9.models import Listing, utcnow

from .models import (
    ChatClassification,
    ChatMessage,
    ChatThread,
    ContactEndpoint,
    EndpointClassification,
)

LADIES_CONFIRMED = "LADIES_CONFIRMED"
UNKNOWN = "UNKNOWN"
NON_LADIES = "NON_LADIES"
VALID_STATUSES = {LADIES_CONFIRMED, UNKNOWN, NON_LADIES}
LADIES_SOURCE = "LADIES"


def person_is_ladies(db, person_id: int | None) -> bool:
    if person_id is None:
        return False
    return db.scalar(
        select(Listing.listing_id)
        .where(
            Listing.person_id == int(person_id),
            Listing.source == LADIES_SOURCE,
        )
        .limit(1)
    ) is not None


def endpoint_ids_for_thread(db, thread: ChatThread) -> list[int]:
    """Return every endpoint owned by a conversation, even before it has messages."""

    values = {
        int(value)
        for value in db.scalars(
            select(ChatMessage.endpoint_id)
            .where(
                ChatMessage.thread_id == int(thread.thread_id),
                ChatMessage.endpoint_id.is_not(None),
            )
            .distinct()
        ).all()
        if value is not None
    }
    if thread.person_id is not None:
        values.update(
            int(value)
            for value in db.scalars(
                select(ContactEndpoint.endpoint_id).where(
                    ContactEndpoint.person_id == int(thread.person_id),
                    ContactEndpoint.provider == "WHATSAPP",
                )
            ).all()
        )
    return sorted(values)


def _ensure_thread_row(db, thread: ChatThread) -> ChatClassification:
    row = db.get(ChatClassification, int(thread.thread_id))
    if row is not None:
        return row
    now = utcnow()
    db.execute(
        sqlite_insert(ChatClassification)
        .values(
            thread_id=int(thread.thread_id),
            status=UNKNOWN,
            method="bootstrap_unknown",
            decided_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["thread_id"])
    )
    db.flush()
    row = db.get(ChatClassification, int(thread.thread_id))
    if row is None:
        raise RuntimeError("Chat-Klassifizierung konnte nicht angelegt werden")
    return row


def _ensure_endpoint_row(db, endpoint_id: int) -> EndpointClassification:
    row = db.get(EndpointClassification, int(endpoint_id))
    if row is not None:
        return row
    now = utcnow()
    db.execute(
        sqlite_insert(EndpointClassification)
        .values(
            endpoint_id=int(endpoint_id),
            status=UNKNOWN,
            method="bootstrap_unknown",
            decided_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["endpoint_id"])
    )
    db.flush()
    row = db.get(EndpointClassification, int(endpoint_id))
    if row is None:
        raise RuntimeError("Endpoint-Klassifizierung konnte nicht angelegt werden")
    return row


def endpoint_status_in_session(db, endpoint_id: int) -> str:
    return _ensure_endpoint_row(db, int(endpoint_id)).status


def thread_status_in_session(db, thread: ChatThread) -> str:
    return _ensure_thread_row(db, thread).status


def _promote_unknown_endpoints(
    endpoint_rows: Iterable[EndpointClassification],
    status: str,
    method: str,
) -> None:
    now = utcnow()
    for item in endpoint_rows:
        if item.status != UNKNOWN:
            continue
        item.status = status
        item.method = method
        item.decided_at = now
        item.updated_at = now


def _detach_non_ladies_identity(db, thread: ChatThread, endpoint_ids: Iterable[int]) -> None:
    """Make the exclusion boundary structural, not just a UI flag."""

    thread.person_id = None
    now = utcnow()
    for endpoint_id in endpoint_ids:
        endpoint = db.get(ContactEndpoint, int(endpoint_id))
        if endpoint is None:
            continue
        endpoint.person_id = None
        endpoint.updated_at = now


def set_thread_classification_in_session(
    db,
    thread: ChatThread,
    status: str,
    *,
    method: str,
    force_endpoints: bool,
    additional_endpoint_ids: Iterable[int] = (),
) -> ChatClassification:
    status = str(status or "").upper()
    if status not in VALID_STATUSES:
        raise ValueError("Ungültige Chat-Klassifizierung")
    method = str(method or "manual")[:48]
    row = _ensure_thread_row(db, thread)
    now = utcnow()
    row.status = status
    row.method = method
    row.decided_at = now
    row.updated_at = now

    endpoint_ids = set(endpoint_ids_for_thread(db, thread))
    endpoint_ids.update(int(value) for value in additional_endpoint_ids if value)
    for endpoint_id in endpoint_ids:
        endpoint_row = _ensure_endpoint_row(db, endpoint_id)
        if force_endpoints or endpoint_row.status == UNKNOWN:
            endpoint_row.status = status
            endpoint_row.method = method
            endpoint_row.decided_at = now
            endpoint_row.updated_at = now

    if status == NON_LADIES:
        _detach_non_ladies_identity(db, thread, endpoint_ids)
    return row


def reconcile_thread_in_session(
    db,
    thread: ChatThread,
    *,
    additional_endpoint_ids: Iterable[int] = (),
) -> ChatClassification:
    """Apply sticky product classification inside an existing write transaction.

    NON_LADIES is fail-closed and structurally detached from Person identity. That
    prevents later search/profile sync and person merges from accidentally pulling
    a private WhatsApp conversation back into the Ladies inbox. Explicit manual
    assignment is the only supported way to attach it again.
    """

    row = _ensure_thread_row(db, thread)
    endpoint_ids = set(endpoint_ids_for_thread(db, thread))
    endpoint_ids.update(int(value) for value in additional_endpoint_ids if value)
    endpoint_rows = [_ensure_endpoint_row(db, endpoint_id) for endpoint_id in sorted(endpoint_ids)]

    if any(item.status == NON_LADIES for item in endpoint_rows):
        if row.status != NON_LADIES:
            now = utcnow()
            row.status = NON_LADIES
            row.method = "endpoint_exclusion_fail_closed"
            row.decided_at = now
            row.updated_at = now
        _promote_unknown_endpoints(endpoint_rows, NON_LADIES, "sticky_endpoint_exclusion")
        _detach_non_ladies_identity(db, thread, endpoint_ids)
        return row

    if row.status == NON_LADIES:
        _promote_unknown_endpoints(endpoint_rows, NON_LADIES, "sticky_thread_exclusion")
        _detach_non_ladies_identity(db, thread, endpoint_ids)
        return row

    if row.status == LADIES_CONFIRMED:
        _promote_unknown_endpoints(endpoint_rows, LADIES_CONFIRMED, "sticky_thread_confirmation")
        return row

    if any(item.status == LADIES_CONFIRMED for item in endpoint_rows):
        return set_thread_classification_in_session(
            db,
            thread,
            LADIES_CONFIRMED,
            method="sticky_endpoint_confirmation",
            force_endpoints=False,
            additional_endpoint_ids=endpoint_ids,
        )

    if person_is_ladies(db, thread.person_id):
        return set_thread_classification_in_session(
            db,
            thread,
            LADIES_CONFIRMED,
            method="persisted_ladies_identity",
            force_endpoints=False,
            additional_endpoint_ids=endpoint_ids,
        )

    return row


class MessagingClassifier:
    """Persistent product classification for WhatsApp-backed conversations.

    Provider sync remains complete. Product reads are side-effect free; backfill
    and reconciliation happen only at startup and identity-changing write paths.
    Automatic transitions may promote UNKNOWN, but never undo explicit
    LADIES_CONFIRMED/NON_LADIES decisions.
    """

    def __init__(self, database):
        self.database = database
        self.reconcile()

    def reconcile(self) -> None:
        with self.database.session() as db:
            for thread in db.scalars(select(ChatThread)).all():
                reconcile_thread_in_session(db, thread)

    def set_thread(self, thread_id: int, status: str, *, method: str) -> dict[str, Any]:
        status = str(status or "").upper()
        if status not in VALID_STATUSES:
            raise ValueError("Ungültige Chat-Klassifizierung")

        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            if status == LADIES_CONFIRMED and not person_is_ladies(db, thread.person_id):
                raise ValueError("Ein Ladies-Chat muss einer Person mit Ladies.de-Provenienz zugeordnet sein")
            row = set_thread_classification_in_session(
                db,
                thread,
                status,
                method=method,
                force_endpoints=True,
            )
            return {
                "thread_id": int(thread.thread_id),
                "person_id": int(thread.person_id) if thread.person_id is not None else None,
                "classification": row.status,
                "classification_method": row.method,
            }

    def classify_person_thread(self, person_id: int, *, method: str) -> dict[str, Any] | None:
        with self.database.session() as db:
            if not person_is_ladies(db, int(person_id)):
                raise ValueError("Person besitzt keine Ladies.de-Provenienz")
            thread = db.scalar(select(ChatThread).where(ChatThread.person_id == int(person_id)).limit(1))
            if thread is None:
                return None
            row = set_thread_classification_in_session(
                db,
                thread,
                LADIES_CONFIRMED,
                method=method,
                force_endpoints=True,
            )
            return {
                "thread_id": int(thread.thread_id),
                "person_id": int(person_id),
                "classification": row.status,
                "classification_method": row.method,
            }

    def _classification_map(self, thread_ids: list[int]) -> dict[int, ChatClassification]:
        if not thread_ids:
            return {}
        with self.database.session() as db:
            rows = db.scalars(
                select(ChatClassification).where(ChatClassification.thread_id.in_(thread_ids))
            ).all()
            return {int(row.thread_id): row for row in rows}

    def filter_items(self, items: list[dict[str, Any]], *, view: str) -> list[dict[str, Any]]:
        view = str(view or "INBOX").upper()
        thread_ids = [int(item.get("thread_id") or 0) for item in items if item.get("thread_id")]
        rows = self._classification_map(thread_ids)
        output: list[dict[str, Any]] = []
        for item in items:
            thread_id = int(item.get("thread_id") or 0)
            row = rows.get(thread_id)
            status = row.status if row is not None else UNKNOWN
            if view == "INBOX" and status != LADIES_CONFIRMED:
                continue
            if view == "REVIEW" and status != UNKNOWN:
                continue
            if view == "NON_LADIES" and status != NON_LADIES:
                continue
            decorated = dict(item)
            decorated["classification"] = status
            decorated["classification_method"] = row.method if row is not None else "bootstrap_unknown"
            output.append(decorated)
        return output

    def summary(self) -> dict[str, int]:
        with self.database.session() as db:
            rows = db.scalars(select(ChatClassification)).all()
        counts = {LADIES_CONFIRMED: 0, UNKNOWN: 0, NON_LADIES: 0}
        for row in rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        return {
            "ladies": counts.get(LADIES_CONFIRMED, 0),
            "unassigned": counts.get(UNKNOWN, 0),
            "non_ladies": counts.get(NON_LADIES, 0),
        }


__all__ = [
    "MessagingClassifier",
    "LADIES_CONFIRMED",
    "UNKNOWN",
    "NON_LADIES",
    "LADIES_SOURCE",
    "person_is_ladies",
    "endpoint_ids_for_thread",
    "endpoint_status_in_session",
    "thread_status_in_session",
    "set_thread_classification_in_session",
    "reconcile_thread_in_session",
]
