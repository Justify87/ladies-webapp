from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import orjson
from sqlalchemy import delete, desc, func, select

from ladies_v9.database import Database
from ladies_v9.media import ArchiveManager
from ladies_v9.models import MediaAsset, VisualAnalysis, utcnow


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


class ArchiveRepository:
    """Persistence/read boundary for archived media and visual analysis."""

    def __init__(self, database: Database, archive: ArchiveManager):
        self.database = database
        self.archive = archive

    @staticmethod
    def _directory_size(path) -> int:
        total = 0
        try:
            for child in path.rglob("*"):
                if child.is_file():
                    try:
                        total += child.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
        return total

    def storage_info(self) -> dict[str, Any]:
        db_bytes = 0
        try:
            db_bytes = self.database.path.stat().st_size
        except OSError:
            pass
        with self.database.session() as db:
            image_count = db.scalar(
                select(func.count(MediaAsset.media_asset_id)).where(MediaAsset.kind == "IMAGE")
            ) or 0
            video_count = db.scalar(
                select(func.count(MediaAsset.media_asset_id)).where(MediaAsset.kind == "VIDEO")
            ) or 0
            visual_count = db.scalar(select(func.count(VisualAnalysis.visual_analysis_id))) or 0
        return {
            "database_bytes": int(db_bytes),
            "archive_bytes": int(self._directory_size(self.archive.data_dir)),
            "media_bytes": int(self._directory_size(self.archive.media_dir)),
            "raw_html_bytes": int(self._directory_size(self.archive.raw_dir)),
            "image_count": int(image_count),
            "video_count": int(video_count),
            "visual_analysis_count": int(visual_count),
        }

    def items(self, *, kind=None, limit: int = 80) -> list[dict[str, Any]]:
        limit = max(1, min(300, int(limit)))
        with self.database.session() as db:
            query = select(MediaAsset)
            if kind:
                query = query.where(MediaAsset.kind == str(kind).upper())
            rows = db.scalars(query.order_by(desc(MediaAsset.last_seen_at)).limit(limit)).all()
            return [
                {
                    "media_asset_id": row.media_asset_id,
                    "kind": row.kind,
                    "mime_type": row.mime_type,
                    "byte_size": row.byte_size,
                    "width": row.width,
                    "height": row.height,
                    "first_seen_at": _iso(row.first_seen_at),
                    "last_seen_at": _iso(row.last_seen_at),
                    "local_url": f"/archive/{row.local_path}" if row.local_path else None,
                }
                for row in rows
            ]

    def clear_visual_analyses(self) -> int:
        with self.database.session() as db:
            count = db.scalar(select(func.count(VisualAnalysis.visual_analysis_id))) or 0
            db.execute(delete(VisualAnalysis))
            return int(count)

    def save_visual_analysis(self, media_asset_id: int, payload) -> dict[str, Any]:
        data = payload or {}
        engine = str(data.get("engine") or "mediapipe-browser")[:64]
        version = str(data.get("engine_version") or "1")[:64]
        with self.database.session() as db:
            if db.get(MediaAsset, int(media_asset_id)) is None:
                raise KeyError(media_asset_id)
            row = db.scalar(
                select(VisualAnalysis).where(
                    VisualAnalysis.media_asset_id == int(media_asset_id),
                    VisualAnalysis.engine == engine,
                    VisualAnalysis.engine_version == version,
                )
            )
            if row is None:
                row = VisualAnalysis(
                    media_asset_id=int(media_asset_id),
                    engine=engine,
                    engine_version=version,
                )
                db.add(row)
            row.score = float(data["score"]) if data.get("score") is not None else None
            row.label = str(data.get("label") or "")[:64] or None
            row.reasons_json = _dumps(data.get("reasons") or [])
            row.diagnostics_json = _dumps(data.get("diagnostics") or {})
            row.checked_at = utcnow()
            db.flush()
            return {
                "media_asset_id": int(media_asset_id),
                "engine": row.engine,
                "engine_version": row.engine_version,
                "score": row.score,
                "label": row.label,
                "reasons": _loads(row.reasons_json, []),
                "checked_at": _iso(row.checked_at),
            }
