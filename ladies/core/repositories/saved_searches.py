from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import orjson
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, delete, select, update
from sqlalchemy.orm import Mapped, mapped_column

from ladies_v9.database import Database
from ladies_v9.models import Base, utcnow


def _dumps(value: Any) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode("utf-8")


def _loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return orjson.loads(value)
    except Exception:
        return default


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class SavedSearchRecord(Base):
    __tablename__ = "saved_searches"

    saved_search_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    filters_json: Mapped[str] = mapped_column(Text, nullable=False)
    definition_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_delta_json: Mapped[str | None] = mapped_column(Text)
    unread_new_results: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    new_results_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SavedSearchResultRecord(Base):
    __tablename__ = "saved_search_results"
    __table_args__ = (
        Index("idx_saved_search_result_visible", "saved_search_id", "visible", "position"),
    )

    saved_search_id: Mapped[str] = mapped_column(
        ForeignKey("saved_searches.saved_search_id", ondelete="CASCADE"),
        primary_key=True,
    )
    result_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    first_visible_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    present: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    visible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SavedSearchRepository:
    """Dedicated persistence for saved-search definitions and result history."""

    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _item(row: SavedSearchRecord) -> dict[str, Any]:
        return {
            "id": row.saved_search_id,
            "name": row.name,
            "filters": _loads(row.filters_json, {}) or {},
            "definition_version": int(row.definition_version or 1),
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
            "last_run_at": _iso(row.last_run_at),
            "last_attempt_at": _iso(row.last_attempt_at),
            "last_error": row.last_error,
            "last_delta": _loads(row.last_delta_json),
            "unread_new_results": bool(row.unread_new_results),
            "new_results_at": _iso(row.new_results_at),
        }

    def list(self) -> list[dict[str, Any]]:
        with self.database.session() as db:
            rows = db.scalars(
                select(SavedSearchRecord).order_by(SavedSearchRecord.created_at.desc())
            ).all()
            return [self._item(row) for row in rows]

    def get(self, saved_id: str) -> dict[str, Any] | None:
        with self.database.session() as db:
            row = db.get(SavedSearchRecord, str(saved_id))
            return self._item(row) if row is not None else None

    def create(self, saved_id: str, *, name: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
        now = utcnow()
        with self.database.session() as db:
            db.add(SavedSearchRecord(
                saved_search_id=str(saved_id),
                name=name,
                filters_json=_dumps(filters),
                definition_version=1,
                created_at=now,
                updated_at=now,
            ))
        return self.list()

    def update_definition(
        self,
        saved_id: str,
        *,
        name: str,
        filters: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], bool]:
        with self.database.session() as db:
            row = db.get(SavedSearchRecord, str(saved_id))
            if row is None:
                raise KeyError(saved_id)
            current_filters = _loads(row.filters_json, {}) or {}
            changed = current_filters != filters
            row.name = name
            row.filters_json = _dumps(filters)
            row.updated_at = utcnow()
            if changed:
                row.definition_version = int(row.definition_version or 1) + 1
                row.last_run_at = None
                row.last_attempt_at = None
                row.last_error = None
                row.last_delta_json = None
                row.unread_new_results = False
                row.new_results_at = None
                db.execute(delete(SavedSearchResultRecord).where(
                    SavedSearchResultRecord.saved_search_id == str(saved_id)
                ))
        return self.list(), changed

    def delete(self, saved_id: str) -> list[dict[str, Any]]:
        with self.database.session() as db:
            row = db.get(SavedSearchRecord, str(saved_id))
            if row is None:
                raise KeyError(saved_id)
            db.delete(row)
        return self.list()

    def acknowledge_all(self) -> list[dict[str, Any]]:
        with self.database.session() as db:
            db.execute(
                update(SavedSearchRecord)
                .where(SavedSearchRecord.unread_new_results.is_(True))
                .values(unread_new_results=False)
            )
        return self.list()

    def record_success(
        self,
        saved_id: str,
        *,
        definition_version: int,
        results: list[dict[str, Any]],
        visible_keys: set[str],
        mark_seen: bool,
    ) -> dict[str, Any]:
        now = utcnow()
        with self.database.session() as db:
            search = db.get(SavedSearchRecord, str(saved_id))
            if search is None or int(search.definition_version or 1) != int(definition_version):
                return {"stale": True, "delta": None}

            existing_rows = db.scalars(
                select(SavedSearchResultRecord).where(
                    SavedSearchResultRecord.saved_search_id == str(saved_id)
                )
            ).all()
            existing = {row.result_key: row for row in existing_rows}
            previous_visible = {
                key for key, row in existing.items() if row.present and row.visible
            }
            initial = search.last_run_at is None

            db.execute(
                update(SavedSearchResultRecord)
                .where(SavedSearchResultRecord.saved_search_id == str(saved_id))
                .values(present=False, visible=False)
            )

            current_visible: set[str] = set()
            new_keys: list[str] = []
            for position, payload in enumerate(results, start=1):
                key = str(payload.get("advert_id") or payload.get("external_listing_id") or "").strip()
                if not key:
                    continue
                visible = key in visible_keys
                row = existing.get(key)
                if row is None:
                    row = SavedSearchResultRecord(
                        saved_search_id=str(saved_id),
                        result_key=key,
                        snapshot_json=_dumps(payload),
                        position=position,
                        first_seen_at=now,
                        last_seen_at=now,
                        present=True,
                        visible=visible,
                    )
                    db.add(row)
                    existing[key] = row
                else:
                    row.snapshot_json = _dumps(payload)
                    row.position = position
                    row.last_seen_at = now
                    row.present = True
                    row.visible = visible
                if visible:
                    current_visible.add(key)
                    if row.first_visible_at is None:
                        row.first_visible_at = now
                        if not initial:
                            new_keys.append(key)

            removed_keys = [] if initial else sorted(previous_visible - current_visible)
            delta = {
                "initial": bool(initial),
                "current_result_count": len(current_visible),
                "new_results_count": len(new_keys),
                "removed_results_count": len(removed_keys),
                "new_result_keys": new_keys,
            }
            has_new = bool(new_keys)
            search.last_attempt_at = now
            search.last_run_at = now
            search.last_error = None
            search.last_delta_json = _dumps(delta)
            search.unread_new_results = False if mark_seen else bool(search.unread_new_results or has_new)
            if has_new:
                search.new_results_at = now

        return {"stale": False, "delta": delta}

    def record_failure(self, saved_id: str, *, definition_version: int, error: str) -> bool:
        with self.database.session() as db:
            search = db.get(SavedSearchRecord, str(saved_id))
            if search is None or int(search.definition_version or 1) != int(definition_version):
                return False
            search.last_attempt_at = utcnow()
            search.last_error = str(error or "Saved Search fehlgeschlagen")[:1000]
            return True

    def results(self, saved_id: str) -> list[dict[str, Any]]:
        with self.database.session() as db:
            if db.get(SavedSearchRecord, str(saved_id)) is None:
                raise KeyError(saved_id)
            rows = db.scalars(
                select(SavedSearchResultRecord)
                .where(
                    SavedSearchResultRecord.saved_search_id == str(saved_id),
                    SavedSearchResultRecord.present.is_(True),
                    SavedSearchResultRecord.visible.is_(True),
                )
                .order_by(SavedSearchResultRecord.position.asc())
            ).all()
            return [_loads(row.snapshot_json, {}) or {} for row in rows]

    def result(self, saved_id: str, result_key: str) -> dict[str, Any] | None:
        with self.database.session() as db:
            row = db.get(SavedSearchResultRecord, (str(saved_id), str(result_key)))
            if row is None or not row.present or not row.visible:
                return None
            return _loads(row.snapshot_json, {}) or {}
