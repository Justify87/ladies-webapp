from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, desc, select
from sqlalchemy.orm import Mapped, mapped_column

from ladies_v9.database import Database
from ladies_v9.models import Base, Encounter, PersonUserState, utcnow

from .persons import PersonRepository


_REPEAT_INTEREST_VALUES = {"UNKNOWN", "YES", "MAYBE", "NO"}


class EncounterUserData(Base):
    """Evaluation fields that belong to one concrete encounter."""

    __tablename__ = "encounter_user_data"

    encounter_id: Mapped[int] = mapped_column(
        ForeignKey("encounters.encounter_id", ondelete="CASCADE"),
        primary_key=True,
    )
    repeat_interest: Mapped[str] = mapped_column(
        String(32), default="UNKNOWN", nullable=False
    )
    private_note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class EncounterDataRepository:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _repeat_interest(value: Any) -> str:
        normalized = str(value or "UNKNOWN").upper()
        if normalized not in _REPEAT_INTEREST_VALUES:
            raise ValueError("Ungültiges Wiederholungsinteresse")
        return normalized

    @staticmethod
    def _private_note(value: Any) -> str | None:
        text = str(value or "").strip()
        return text[:10000] or None

    @staticmethod
    def _decorate(encounter: dict[str, Any], row: EncounterUserData | None) -> dict[str, Any]:
        result = dict(encounter or {})
        result["repeat_interest"] = row.repeat_interest if row else "UNKNOWN"
        result["private_note"] = row.private_note if row and row.private_note else ""
        return result

    def save(self, encounter_id: int, payload: dict[str, Any] | None) -> dict[str, Any]:
        data = payload or {}
        has_extra = "repeat_interest" in data or "private_note" in data
        with self.database.session() as db:
            encounter = db.get(Encounter, int(encounter_id))
            if encounter is None:
                raise KeyError(encounter_id)
            row = db.get(EncounterUserData, int(encounter_id))
            if row is None and not has_extra:
                return {"repeat_interest": "UNKNOWN", "private_note": ""}
            if row is None:
                row = EncounterUserData(encounter_id=int(encounter_id))
                db.add(row)
            if "repeat_interest" in data:
                row.repeat_interest = self._repeat_interest(data.get("repeat_interest"))
            if "private_note" in data:
                row.private_note = self._private_note(data.get("private_note"))
            row.updated_at = utcnow()
            db.flush()
            return {
                "repeat_interest": row.repeat_interest,
                "private_note": row.private_note or "",
            }

    def decorate_encounter(self, encounter: dict[str, Any]) -> dict[str, Any]:
        encounter_id = int((encounter or {}).get("encounter_id") or 0)
        if not encounter_id:
            return self._decorate(encounter, None)
        with self.database.session() as db:
            return self._decorate(encounter, db.get(EncounterUserData, encounter_id))

    def decorate_person_detail(self, detail: dict[str, Any]) -> dict[str, Any]:
        result = dict(detail or {})
        encounters = [dict(item) for item in (result.get("encounters") or [])]
        if not encounters:
            return result
        ids = [int(item.get("encounter_id") or 0) for item in encounters]
        ids = [value for value in ids if value]
        rows: dict[int, EncounterUserData] = {}
        if ids:
            with self.database.session() as db:
                rows = {
                    row.encounter_id: row
                    for row in db.scalars(
                        select(EncounterUserData).where(EncounterUserData.encounter_id.in_(ids))
                    ).all()
                }
        legacy = result.get("user_state") or {}
        decorated: list[dict[str, Any]] = []
        for index, encounter in enumerate(encounters):
            encounter_id = int(encounter.get("encounter_id") or 0)
            row = rows.get(encounter_id)
            item = self._decorate(encounter, row)
            if row is None and index == 0:
                item["repeat_interest"] = str(legacy.get("repeat_interest") or "UNKNOWN")
                item["private_note"] = str(legacy.get("private_note") or "")
            decorated.append(item)
        result["encounters"] = decorated
        return result

    def backfill_legacy(self) -> None:
        """Move legacy person-level fields to the newest existing encounter.

        Idempotent by primary key. People without an encounter keep their legacy
        values as a UI fallback until their first encounter is created.
        """
        with self.database.session() as db:
            states = db.scalars(select(PersonUserState)).all()
            for state in states:
                if (
                    str(state.repeat_interest or "UNKNOWN").upper() == "UNKNOWN"
                    and not state.private_note
                ):
                    continue
                encounter = db.scalar(
                    select(Encounter)
                    .where(Encounter.person_id == state.person_id)
                    .order_by(desc(Encounter.occurred_at), desc(Encounter.encounter_id))
                    .limit(1)
                )
                if encounter is None or db.get(EncounterUserData, encounter.encounter_id):
                    continue
                db.add(EncounterUserData(
                    encounter_id=encounter.encounter_id,
                    repeat_interest=self._repeat_interest(state.repeat_interest),
                    private_note=self._private_note(state.private_note),
                    updated_at=utcnow(),
                ))


class EncounterAwarePersonRepository(PersonRepository):
    """Person facade that keeps encounter-specific evaluation data together."""

    def __init__(self, database, archive, settings_repository, read_store, encounter_data):
        super().__init__(database, archive, settings_repository, read_store)
        self._encounter_data = encounter_data

    def person_detail(self, person_id):
        return self._encounter_data.decorate_person_detail(super().person_detail(person_id))

    def add_encounter(self, person_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        encounter = super().add_encounter(person_id, payload)
        self._encounter_data.save(encounter["encounter_id"], payload)
        return self._encounter_data.decorate_encounter(encounter)

    def update_encounter(
        self,
        person_id: int,
        encounter_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        encounter = super().update_encounter(person_id, encounter_id, payload)
        self._encounter_data.save(encounter_id, payload)
        return self._encounter_data.decorate_encounter(encounter)
