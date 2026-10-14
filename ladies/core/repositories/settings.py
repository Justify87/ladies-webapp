from __future__ import annotations

from typing import Any

import orjson
from sqlalchemy import select

from ladies_v9.database import Database
from ladies_v9.models import AppSetting, utcnow


DEFAULT_SETTINGS = {
    "archive_videos": False,
    "archive_raw_html": True,
    "visual_check_enabled": True,
    "show_description_changes": False,
    "replay_likes": True,
    "autoplay_videos": False,
    "reduced_motion": False,
    "default_filters": None,
    "saved_searches": [],
}


def _dumps(value: Any) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode("utf-8")


def _loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return orjson.loads(value)
    except Exception:
        return default


class SettingsRepository:
    """Persistence boundary for application settings.

    This repository no longer delegates through the legacy aggregate Repository;
    callers depend directly on the shared Database contract.
    """

    def __init__(self, database: Database):
        self.database = database
        self._ensure_defaults()

    def _ensure_defaults(self) -> None:
        with self.database.session() as db:
            for key, value in DEFAULT_SETTINGS.items():
                if db.get(AppSetting, key) is None:
                    db.add(AppSetting(key=key, value_json=_dumps(value)))

    def get_all(self) -> dict[str, Any]:
        with self.database.session() as db:
            rows = db.scalars(select(AppSetting)).all()
            out = dict(DEFAULT_SETTINGS)
            out.update({row.key: _loads(row.value_json) for row in rows})
            return out

    def set(self, key: str, value: Any) -> dict[str, Any]:
        if key not in DEFAULT_SETTINGS:
            raise ValueError(f"Unbekannte Einstellung: {key}")
        with self.database.session() as db:
            row = db.get(AppSetting, key)
            if row is None:
                db.add(AppSetting(key=key, value_json=_dumps(value)))
            else:
                row.value_json = _dumps(value)
                row.updated_at = utcnow()
        return self.get_all()
