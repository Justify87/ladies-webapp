from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ladies_v9.models import Listing, utcnow

from .models import (
    ChatClassification,
    ChatMessage,
    ChatThread,
    EndpointClassification,
)

LADIES_CONFIRMED = "LADIES_CONFIRMED"
UNKNOWN = "UNKNOWN"
NON_LADIES = "NON_LADIES"
VALID_STATUSES = {LADIES_CONFIRMED, UNKNOWN, NON_LADIES}
LADIES_SOURCE = "LADIES"


class MessagingClassifier:
    """Persistent product classification for WhatsApp-backed conversations.

    Provider sync remains complete. This layer decides which persisted chats belong
    in the Ladies inbox. Decisions are sticky: automatic reconciliation only
    promotes an undecided UNKNOWN chat; it never overwrites an explicit
    LADIES_CONFIRMED or NON_LADIES decision.
    """

    def __init__(self, database):
        self.database = database
        self.reconcile()

    @staticmethod
    def _endpoint_ids(db, thread_id: int) -> list[int]:
        return [
            int(value)
            for value in db.scalars(
                select(ChatMessage.endpoint_id)
                .where(
                    ChatMessage.thread_id == int(thread_id),
                    ChatMessage.endpoint_id.is_not(None),
                )
                .distinct()
            ).all()
            if value is not None
        ]

    @staticmethod
    def _person_is_ladies(db, person_id: int | None) -> bool:
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

    @staticmethod
    def _ensure_thread_row(db, thread: ChatThread) -> ChatClassification:
        row = db.get(ChatClassification, int(thread.thread_id))
        if row is None:
            row = ChatClassification(
                thread_id=int(thread.thread_id),
                status=UNKNOWN,
                method="bootstrap_unknown",
            )
            db.add(row)
            db.flush()
        return row

    @staticmethod
    def _ensure_endpoint_row(db, endpoint_id: int) -> EndpointClassification:
        row = db.get(EndpointClassification, int(endpoint_id))
        if row is None:
            row = EndpointClassification(
                endpoint_id=int(endpoint_id),
                status=UNKNOWN,
                method="bootstrap_unknown",
            )
            db.add(row)
            db.flush()
        return row

    def reconcile(self) -> None:
        """Backfill and promote only durable Ladies identity evidence.

        A Person link alone is not enough: automatic confirmation additionally
        requires persistent LADIES listing provenance. A persisted NON_LADIES
        endpoint wins over newly inferred identity until the user explicitly
        confirms the chat again.
        """

        with self.database.session() as db:
            threads = db.scalars(select(ChatThread)).all()
            for thread in threads:
                row = self._ensure_thread_row(db, thread)
                endpoint_ids = self._endpoint_ids(db, thread.thread_id)
                endpoint_rows = [self._ensure_endpoint_row(db, endpoint_id) for endpoint_id in endpoint_ids]

                if row.status in {LADIES_CONFIRMED, NON_LADIES}:
                    continue
                if any(item.status == NON_LADIES for item in endpoint_rows):
                    row.status = NON_LADIES
                    row.method = "sticky_endpoint_exclusion"
                    row.decided_at = utcnow()
                    row.updated_at = utcnow()
                    continue
                if any(item.status == LADIES_CONFIRMED for item in endpoint_rows):
                    row.status = LADIES_CONFIRMED
                    row.method = "sticky_endpoint_confirmation"
                    row.decided_at = utcnow()
                    row.updated_at = utcnow()
                    continue
                if self._person_is_ladies(db, thread.person_id):
                    row.status = LADIES_CONFIRMED
                    row.method = "persisted_ladies_identity"
                    row.decided_at = utcnow()
                    row.updated_at = utcnow()
                    for item in endpoint_rows:
                        if item.status == UNKNOWN:
                            item.status = LADIES_CONFIRMED
                            item.method = "persisted_ladies_identity"
                            item.decided_at = utcnow()
                            item.updated_at = utcnow()

    def set_thread(self, thread_id: int, status: str, *, method: str) -> dict[str, Any]:
        status = str(status or "").upper()
        if status not in VALID_STATUSES:
            raise ValueError("Ungültige Chat-Klassifizierung")
        method = str(method or "manual")[:48]

        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            if status == LADIES_CONFIRMED and not self._person_is_ladies(db, thread.person_id):
                raise ValueError("Ein Ladies-Chat muss einer Person mit Ladies.de-Provenienz zugeordnet sein")

            row = self._ensure_thread_row(db, thread)
            row.status = status
            row.method = method
            row.decided_at = utcnow()
            row.updated_at = utcnow()

            for endpoint_id in self._endpoint_ids(db, thread.thread_id):
                endpoint_row = self._ensure_endpoint_row(db, endpoint_id)
                endpoint_row.status = status
                endpoint_row.method = method
                endpoint_row.decided_at = utcnow()
                endpoint_row.updated_at = utcnow()

            return {
                "thread_id": int(thread.thread_id),
                "person_id": int(thread.person_id) if thread.person_id is not None else None,
                "classification": row.status,
                "classification_method": row.method,
            }

    def classify_person_thread(self, person_id: int, *, method: str) -> dict[str, Any] | None:
        with self.database.session() as db:
            if not self._person_is_ladies(db, int(person_id)):
                raise ValueError("Person besitzt keine Ladies.de-Provenienz")
            thread = db.scalar(select(ChatThread).where(ChatThread.person_id == int(person_id)).limit(1))
            thread_id = int(thread.thread_id) if thread is not None else None
        if thread_id is None:
            return None
        return self.set_thread(thread_id, LADIES_CONFIRMED, method=method)

    def _classification_map(self, thread_ids: list[int]) -> dict[int, ChatClassification]:
        if not thread_ids:
            return {}
        with self.database.session() as db:
            rows = db.scalars(
                select(ChatClassification).where(ChatClassification.thread_id.in_(thread_ids))
            ).all()
            return {int(row.thread_id): row for row in rows}

    def filter_items(self, items: list[dict[str, Any]], *, view: str) -> list[dict[str, Any]]:
        self.reconcile()
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
        self.reconcile()
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
]
