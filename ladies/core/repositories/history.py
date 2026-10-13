from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import orjson
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ladies_v9.database import Database
from ladies_v9.models import (
    ChangeEvent,
    Encounter,
    Listing,
    ListingSnapshot,
    MediaAsset,
    Person,
    PersonUserState,
    SearchResult,
    SearchRun,
    SnapshotMedia,
    VisualAnalysis,
)


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


def _change_payload(row: ChangeEvent | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "change_event_id": row.change_event_id,
        "person_id": row.person_id,
        "listing_id": row.listing_id,
        "from_snapshot_id": row.from_snapshot_id,
        "to_snapshot_id": row.to_snapshot_id,
        "event_type": row.event_type,
        "field_key": row.field_key,
        "old": _loads(row.old_json),
        "new": _loads(row.new_json),
        "importance": row.importance,
        "created_at": _iso(row.created_at),
    }


class HistoryRepository:
    """Read-model boundary for dashboard, change history and snapshots."""

    def __init__(self, database: Database):
        self.database = database

    def dashboard(self) -> dict[str, Any]:
        with self.database.session() as db:
            persons = db.scalar(select(func.count(Person.person_id))) or 0
            listings = db.scalar(select(func.count(Listing.listing_id))) or 0
            active = db.scalar(
                select(func.count(Listing.listing_id)).where(Listing.current_status == "ACTIVE")
            ) or 0
            snapshots = db.scalar(select(func.count(ListingSnapshot.snapshot_id))) or 0
            media = db.scalar(select(func.count(MediaAsset.media_asset_id))) or 0
            changes = db.scalar(select(func.count(ChangeEvent.change_event_id))) or 0
            encounters = db.scalar(select(func.count(Encounter.encounter_id))) or 0
            visual = db.scalar(select(func.count(VisualAnalysis.visual_analysis_id))) or 0
            likes = db.scalar(
                select(func.count(PersonUserState.person_id)).where(PersonUserState.preference == "LIKE")
            ) or 0
            dislikes = db.scalar(
                select(func.count(PersonUserState.person_id)).where(PersonUserState.preference == "DISLIKE")
            ) or 0
            favorites = db.scalar(
                select(func.count(PersonUserState.person_id)).where(PersonUserState.favorite.is_(True))
            ) or 0
            returns = db.scalar(
                select(func.count()).select_from(
                    select(Listing.person_id)
                    .group_by(Listing.person_id)
                    .having(func.count(Listing.listing_id) > 1)
                    .subquery()
                )
            ) or 0
            recent = db.scalars(
                select(ChangeEvent).order_by(desc(ChangeEvent.created_at)).limit(30)
            ).all()
            search_total = db.scalar(
                select(func.count(SearchRun.search_run_id)).where(SearchRun.finished_at.is_not(None))
            ) or 0
            search_rows = db.scalars(
                select(SearchRun)
                .where(SearchRun.finished_at.is_not(None))
                .order_by(desc(SearchRun.started_at))
                .limit(250)
            ).all()

            filter_counts: dict[str, int] = defaultdict(int)
            for run in search_rows:
                filters = _loads(run.filters_json, {}) or {}
                selected = filters.get("selected") if isinstance(filters, dict) else None
                if isinstance(selected, dict):
                    for values in selected.values():
                        if isinstance(values, list):
                            for value in values:
                                if value:
                                    filter_counts[str(value)] += 1
            avg_results = (
                sum(int(row.advert_count or 0) for row in search_rows) / len(search_rows)
                if search_rows else None
            )
            decisions = int(likes) + int(dislikes)
            return {
                "counts": {
                    "persons": int(persons),
                    "listings": int(listings),
                    "active_listings": int(active),
                    "snapshots": int(snapshots),
                    "media_assets": int(media),
                    "people_with_relistings": int(returns),
                    "changes": int(changes),
                    "likes": int(likes),
                    "dislikes": int(dislikes),
                    "favorites": int(favorites),
                    "encounters": int(encounters),
                    "visual_analyses": int(visual),
                },
                "recent_changes": [_change_payload(row) for row in recent],
                "usage": {
                    "searches": int(search_total),
                    "decisions": decisions,
                    "like_rate": round(int(likes) / decisions * 100) if decisions else None,
                    "favorite_rate": round(int(favorites) / int(likes) * 100) if likes else None,
                    "avg_results": round(avg_results, 1) if avg_results is not None else None,
                    "top_filters": [
                        {"label": key, "count": count}
                        for key, count in sorted(
                            filter_counts.items(),
                            key=lambda item: (-item[1], item[0].casefold()),
                        )[:8]
                    ],
                },
            }

    def relistings(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(250, int(limit)))
        with self.database.session() as db:
            person_ids = db.scalars(
                select(Listing.person_id)
                .group_by(Listing.person_id)
                .having(func.count(Listing.listing_id) > 1)
                .order_by(desc(func.max(Listing.last_seen_at)))
                .limit(limit)
            ).all()
            out = []
            for person_id in person_ids:
                person = db.get(Person, person_id)
                listings = db.scalars(
                    select(Listing)
                    .where(Listing.person_id == person_id)
                    .order_by(desc(Listing.last_seen_at))
                ).all()
                latest = listings[0] if listings else None
                snapshot = (
                    db.get(ListingSnapshot, latest.current_snapshot_id)
                    if latest and latest.current_snapshot_id else None
                )
                search_result = (
                    db.scalar(
                        select(SearchResult)
                        .where(SearchResult.listing_id == latest.listing_id)
                        .order_by(desc(SearchResult.observed_at))
                        .limit(1)
                    ) if latest else None
                )
                person_search = db.scalar(
                    select(SearchResult)
                    .join(Listing, SearchResult.listing_id == Listing.listing_id)
                    .where(Listing.person_id == person_id)
                    .order_by(desc(SearchResult.observed_at))
                    .limit(1)
                )
                fallback_snapshot = db.scalar(
                    select(ListingSnapshot)
                    .join(Listing, ListingSnapshot.listing_id == Listing.listing_id)
                    .where(
                        Listing.person_id == person_id,
                        ListingSnapshot.city.is_not(None),
                    )
                    .order_by(desc(ListingSnapshot.captured_at))
                    .limit(1)
                )
                thumb = None
                if snapshot:
                    media = db.scalar(
                        select(SnapshotMedia)
                        .where(
                            SnapshotMedia.snapshot_id == snapshot.snapshot_id,
                            SnapshotMedia.kind == "IMAGE",
                        )
                        .order_by(SnapshotMedia.position)
                        .limit(1)
                    )
                    if media:
                        if media.media_asset_id:
                            asset = db.get(MediaAsset, media.media_asset_id)
                            thumb = f"/archive/{asset.local_path}" if asset else media.remote_url
                        else:
                            thumb = media.remote_url
                if not thumb and search_result:
                    thumb = search_result.thumbnail_url
                out.append({
                    "person_id": person_id,
                    "name": (
                        (snapshot.name if snapshot and snapshot.name else None)
                        or (search_result.name if search_result and search_result.name else None)
                        or (person_search.name if person_search and person_search.name else None)
                        or (person.display_name if person else None)
                    ),
                    "city": (
                        (snapshot.city if snapshot and snapshot.city else None)
                        or (fallback_snapshot.city if fallback_snapshot and fallback_snapshot.city else None)
                        or (search_result.city if search_result and search_result.city else None)
                        or (person_search.city if person_search and person_search.city else None)
                    ),
                    "thumbnail_url": thumb or (person_search.thumbnail_url if person_search else None),
                    "listing_count": len(listings),
                    "last_seen_at": (
                        _iso(person.last_seen_at) if person
                        else (_iso(latest.last_seen_at) if latest else None)
                    ),
                })
            return out

    def changes(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(250, int(limit)))
        with self.database.session() as db:
            rows = db.scalars(
                select(ChangeEvent).order_by(desc(ChangeEvent.created_at)).limit(limit)
            ).all()
            return [_change_payload(row) for row in rows]

    def snapshot_detail(self, snapshot_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            snapshot = db.get(ListingSnapshot, int(snapshot_id))
            if snapshot is None:
                raise KeyError(snapshot_id)
            listing = db.get(Listing, snapshot.listing_id)
            return {
                "snapshot": self._snapshot_payload(db, snapshot),
                "listing": self._listing_payload(db, listing) if listing else None,
            }

    @staticmethod
    def _listing_payload(db: Session, listing: Listing) -> dict[str, Any]:
        snapshot = db.get(ListingSnapshot, listing.current_snapshot_id) if listing.current_snapshot_id else None
        return {
            "listing_id": listing.listing_id,
            "advert_id": listing.external_listing_id,
            "person_id": listing.person_id,
            "source_profile_id": listing.source_profile_id,
            "current_url": listing.current_url,
            "category": listing.category,
            "published_at": _iso(listing.published_at),
            "first_seen_at": _iso(listing.first_seen_at),
            "last_seen_at": _iso(listing.last_seen_at),
            "current_status": listing.current_status,
            "status_checked_at": _iso(listing.status_checked_at),
            "ended_at": _iso(listing.ended_at),
            "match_decision": listing.match_decision,
            "candidate_person_id": listing.candidate_person_id,
            "match_reason": listing.match_reason,
            "match_best_distance": listing.match_best_distance,
            "match_image_count": listing.match_image_count,
            "match_strong_image_count": listing.match_strong_image_count,
            "match_phone": listing.match_phone,
            "match_source_profile": listing.match_source_profile,
            "name": snapshot.name if snapshot else None,
            "city": snapshot.city if snapshot else None,
            "snapshot_id": snapshot.snapshot_id if snapshot else None,
        }

    @staticmethod
    def _snapshot_payload(db: Session, snapshot: ListingSnapshot) -> dict[str, Any]:
        media_rows = db.execute(
            select(SnapshotMedia, MediaAsset)
            .outerjoin(MediaAsset, MediaAsset.media_asset_id == SnapshotMedia.media_asset_id)
            .where(SnapshotMedia.snapshot_id == snapshot.snapshot_id)
            .order_by(SnapshotMedia.position, SnapshotMedia.kind)
        ).all()
        media = []
        for snapshot_media, asset in media_rows:
            visual = None
            if asset:
                analysis = db.scalar(
                    select(VisualAnalysis)
                    .where(VisualAnalysis.media_asset_id == asset.media_asset_id)
                    .order_by(desc(VisualAnalysis.checked_at))
                    .limit(1)
                )
                if analysis:
                    visual = {
                        "engine": analysis.engine,
                        "engine_version": analysis.engine_version,
                        "score": analysis.score,
                        "label": analysis.label,
                        "reasons": _loads(analysis.reasons_json, []),
                        "diagnostics": _loads(analysis.diagnostics_json, {}),
                        "checked_at": _iso(analysis.checked_at),
                    }
            media.append({
                "snapshot_media_id": snapshot_media.snapshot_media_id,
                "media_asset_id": snapshot_media.media_asset_id,
                "kind": snapshot_media.kind,
                "position": snapshot_media.position,
                "role": snapshot_media.role,
                "url": snapshot_media.remote_url,
                "remote_base_url": snapshot_media.remote_base_url,
                "variant": snapshot_media.remote_variant,
                "revision_token": snapshot_media.revision_token,
                "etag": snapshot_media.etag,
                "last_modified": snapshot_media.last_modified,
                "sha256": asset.sha256 if asset else None,
                "dhash": asset.dhash if asset else None,
                "width": asset.width if asset else None,
                "height": asset.height if asset else None,
                "byte_size": asset.byte_size if asset else None,
                "local_url": f"/archive/{asset.local_path}" if asset else None,
                "visual_analysis": visual,
            })
        return {
            "snapshot_id": snapshot.snapshot_id,
            "captured_at": _iso(snapshot.captured_at),
            "fingerprint": snapshot.fingerprint,
            "advert_id": snapshot.listing.external_listing_id if snapshot.listing else None,
            "source_url": snapshot.source_url,
            "name": snapshot.name,
            "city": snapshot.city,
            "postal_code": snapshot.postal_code,
            "hidden_location": snapshot.hidden_location,
            "category": snapshot.category,
            "description": snapshot.description,
            "published_at": _iso(snapshot.published_at),
            "source_profile_id": snapshot.external_profile_id,
            "canonical_id": snapshot.canonical_id,
            "phone_display": snapshot.phone_display,
            "normalized_phone": snapshot.normalized_phone,
            "whatsapp_url": snapshot.whatsapp_url,
            "sms_url": snapshot.sms_url,
            "tel_url": snapshot.tel_url,
            "traits": _loads(snapshot.traits_json, {}),
            "services": _loads(snapshot.services_json, {}),
            "opening_times": _loads(snapshot.opening_times_json, {}),
            "auxiliary": _loads(snapshot.auxiliary_json, {}),
            "media": media,
            "raw_html_sha256": snapshot.raw_html_sha256,
        }
