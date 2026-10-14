from __future__ import annotations

import json
import re
from typing import Any

from flask import request
from sqlalchemy import ForeignKey, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class EncounterUserData(Base):
    """User-entered evaluation fields that belong to one concrete encounter.

    The core Encounter model already owns ratings, duration and pricing. This
    one-to-one extension moves the formerly person-global repeat-interest and
    private note into the same encounter aggregate without rewriting the
    existing encounters table in portable installations.
    """

    __tablename__ = "encounter_user_data"

    encounter_id: Mapped[int] = mapped_column(
        ForeignKey("encounters.encounter_id", ondelete="CASCADE"),
        primary_key=True,
    )
    repeat_interest: Mapped[str] = mapped_column(String(32), default="UNKNOWN", nullable=False)
    private_note: Mapped[str | None] = mapped_column(Text)


_ENCOUNTER_WRITE_RE = re.compile(r"^/api/person/(?P<person_id>\d+)/encounters(?:/(?P<encounter_id>\d+))?$")
_PERSON_READ_RE = re.compile(r"^/api/person/(?P<person_id>\d+)$")


def _json_body(response) -> dict[str, Any] | None:
    if not response.is_json:
        return None
    value = response.get_json(silent=True)
    return value if isinstance(value, dict) else None


def _set_json_body(response, payload: dict[str, Any]):
    response.set_data(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    return response


def _encounter_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    direct = payload.get("encounters")
    if isinstance(direct, list):
        return [item for item in direct if isinstance(item, dict)]
    person = payload.get("person")
    if isinstance(person, dict) and isinstance(person.get("encounters"), list):
        return [item for item in person["encounters"] if isinstance(item, dict)]
    return []


def install_encounter_metadata(app, *, database) -> None:
    """Attach encounter-scoped repeat-interest/private-note to existing APIs.

    Existing encounter POST/PUT endpoints keep owning the main Encounter row.
    This hook persists the two formerly person-global fields after a successful
    write and enriches encounter responses/read models with them.
    """

    @app.after_request
    def encounter_user_data(response):
        if response.status_code >= 400:
            return response

        write_match = _ENCOUNTER_WRITE_RE.match(request.path)
        if write_match and request.method in {"POST", "PUT"}:
            body = _json_body(response)
            encounter = body.get("encounter") if isinstance(body, dict) else None
            encounter_id = int((encounter or {}).get("encounter_id") or write_match.group("encounter_id") or 0)
            submitted = request.get_json(silent=True) or {}
            if encounter_id and isinstance(submitted, dict) and (
                "repeat_interest" in submitted or "private_note" in submitted
            ):
                with database.session() as db:
                    row = db.get(EncounterUserData, encounter_id)
                    if row is None:
                        row = EncounterUserData(encounter_id=encounter_id)
                        db.add(row)
                    if "repeat_interest" in submitted:
                        row.repeat_interest = str(submitted.get("repeat_interest") or "UNKNOWN")[:32]
                    if "private_note" in submitted:
                        row.private_note = str(submitted.get("private_note") or "")[:10000] or None
                    db.flush()
                    repeat_interest = row.repeat_interest or "UNKNOWN"
                    private_note = row.private_note or ""
                if isinstance(encounter, dict):
                    encounter["repeat_interest"] = repeat_interest
                    encounter["private_note"] = private_note
                    _set_json_body(response, body)
            return response

        if request.method == "GET" and _PERSON_READ_RE.match(request.path):
            body = _json_body(response)
            if not isinstance(body, dict):
                return response
            encounters = _encounter_list(body)
            ids = [int(item.get("encounter_id") or 0) for item in encounters]
            ids = [value for value in ids if value > 0]
            user_state = body.get("user_state") if isinstance(body.get("user_state"), dict) else {}
            legacy_repeat = str(user_state.get("repeat_interest") or "UNKNOWN")[:32]
            legacy_note = str(user_state.get("private_note") or "")[:10000] or None
            metadata: dict[int, EncounterUserData] = {}
            if ids:
                with database.session() as db:
                    rows = db.scalars(
                        select(EncounterUserData).where(EncounterUserData.encounter_id.in_(ids))
                    ).all()
                    metadata = {int(row.encounter_id): row for row in rows}
                    latest_id = ids[0]
                    if latest_id not in metadata and (legacy_repeat != "UNKNOWN" or legacy_note):
                        migrated = EncounterUserData(
                            encounter_id=latest_id,
                            repeat_interest=legacy_repeat,
                            private_note=legacy_note,
                        )
                        db.add(migrated)
                        db.flush()
                        metadata[latest_id] = migrated
            for encounter in encounters:
                encounter_id = int(encounter.get("encounter_id") or 0)
                row = metadata.get(encounter_id)
                encounter["repeat_interest"] = row.repeat_interest if row else "UNKNOWN"
                encounter["private_note"] = (row.private_note or "") if row else ""
            _set_json_body(response, body)
        return response
