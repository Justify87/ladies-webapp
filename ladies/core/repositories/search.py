from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import orjson
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ladies_v9.database import Database
from ladies_v9.domain import SearchProfile
from ladies_v9.models import (
    ChangeEvent,
    Listing,
    ListingObservation,
    Person,
    PersonUserState,
    SearchResult,
    SearchRun,
    utcnow,
)

BUSINESS_PROFILE_SOURCE = "BUSINESS_PROFILE"


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


class SearchRepository:
    """Persistence boundary for search runs and canonical search observations."""

    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _business_profile_payload(db: Session, listing: Listing) -> dict[str, Any]:
        observation = db.scalar(
            select(ListingObservation)
            .where(
                ListingObservation.listing_id == listing.listing_id,
                ListingObservation.source == BUSINESS_PROFILE_SOURCE,
            )
            .order_by(desc(ListingObservation.observed_at))
            .limit(1)
        )
        if observation is None:
            return {
                "business_profile_known": False,
                "is_business_profile": False,
                "business_website_url": None,
                "business_profile_checked_at": None,
            }
        details = _loads(observation.details_json, {}) or {}
        if "is_business_profile" not in details:
            return {
                "business_profile_known": False,
                "is_business_profile": False,
                "business_website_url": None,
                "business_profile_checked_at": _iso(observation.observed_at),
            }
        return {
            "business_profile_known": True,
            "is_business_profile": bool(details.get("is_business_profile")),
            "business_website_url": str(details.get("business_website_url") or "").strip() or None,
            "business_profile_checked_at": _iso(observation.observed_at),
        }

    def business_profile_classification(self, advert_id: str) -> dict[str, Any] | None:
        with self.database.session() as db:
            listing = db.scalar(
                select(Listing).where(
                    Listing.source == "LADIES",
                    Listing.external_listing_id == str(advert_id),
                )
            )
            if listing is None:
                return None
            return self._business_profile_payload(db, listing)

    def save_business_profile_classification(
        self,
        advert_id: str,
        *,
        is_business_profile: bool,
        business_website_url: str | None,
        http_status: int = 200,
    ) -> dict[str, Any]:
        with self.database.session() as db:
            listing = db.scalar(
                select(Listing).where(
                    Listing.source == "LADIES",
                    Listing.external_listing_id == str(advert_id),
                )
            )
            if listing is None:
                raise KeyError(advert_id)
            current = self._business_profile_payload(db, listing)
            website = str(business_website_url or "").strip() or None
            if (
                current.get("business_profile_known")
                and bool(current.get("is_business_profile")) == bool(is_business_profile)
                and (current.get("business_website_url") or None) == website
            ):
                return current
            observed_at = utcnow()
            db.add(ListingObservation(
                listing_id=listing.listing_id,
                observed_at=observed_at,
                source=BUSINESS_PROFILE_SOURCE,
                status="CLASSIFIED",
                http_status=int(http_status),
                details_json=_dumps({
                    "is_business_profile": bool(is_business_profile),
                    "business_website_url": website,
                }),
            ))
            db.flush()
            return {
                "business_profile_known": True,
                "is_business_profile": bool(is_business_profile),
                "business_website_url": website,
                "business_profile_checked_at": _iso(observed_at),
            }

    def create_search_run(
        self,
        *,
        city: str,
        city_slug: str,
        radius_km: int,
        filters: dict[str, Any],
    ) -> int:
        with self.database.session() as db:
            row = SearchRun(
                city=city,
                city_slug=city_slug,
                radius_km=radius_km,
                filters_json=_dumps(filters),
            )
            db.add(row)
            db.flush()
            return int(row.search_run_id)

    def finish_search_run(
        self,
        search_run_id: int,
        profiles: list[SearchProfile],
        meta: dict[str, Any],
    ) -> list[dict[str, Any]]:
        now = utcnow()
        result_payload: list[dict[str, Any]] = []
        with self.database.session() as db:
            run = db.get(SearchRun, int(search_run_id))
            if run is None:
                raise ValueError("Search run nicht gefunden")
            run.finished_at = now
            run.advert_count = (
                meta.get("server_advert_count")
                if meta.get("server_advert_count") is not None
                else len(profiles)
            )
            try:
                run.distance_searched = (
                    int(meta.get("distance_searched"))
                    if meta.get("distance_searched") is not None
                    else run.radius_km
                )
            except (TypeError, ValueError):
                run.distance_searched = run.radius_km
            run.metadata_json = _dumps(meta)

            for position, profile in enumerate(profiles, start=1):
                listing = self._ensure_listing_from_search(db, profile, now)
                db.add(SearchResult(
                    search_run_id=run.search_run_id,
                    listing_id=listing.listing_id,
                    position=position,
                    observed_at=now,
                    thumbnail_url=profile.thumbnail_url,
                    name=profile.name,
                    city=profile.city,
                    has_video=profile.has_video,
                    is_premium=profile.is_premium,
                    has_ai_content=profile.has_ai_content,
                    markers_json=_dumps(profile.markers),
                ))
                db.add(ListingObservation(
                    listing_id=listing.listing_id,
                    observed_at=now,
                    source="SEARCH",
                    status="SEEN",
                    search_run_id=run.search_run_id,
                    details_json=_dumps({
                        "position": position,
                        "name": profile.name,
                        "city": profile.city,
                    }),
                ))
                result_payload.append(self._search_payload(db, listing, profile))
        return result_payload

    def sync_search_profiles(self, profiles: list[SearchProfile]) -> list[dict[str, Any]]:
        """Canonicalize search rows locally without creating SearchRun/SearchResult history."""
        now = utcnow()
        result_payload: list[dict[str, Any]] = []
        with self.database.session() as db:
            for profile in profiles:
                listing = self._ensure_listing_from_search(db, profile, now)
                result_payload.append(self._search_payload(db, listing, profile))
        return result_payload

    def recent_searches(self, limit: int = 8) -> list[dict[str, Any]]:
        limit = max(1, min(30, int(limit)))
        with self.database.session() as db:
            rows = db.scalars(
                select(SearchRun)
                .where(SearchRun.finished_at.is_not(None))
                .order_by(desc(SearchRun.started_at))
                .limit(limit * 5)
            ).all()
            seen: set[tuple[str, int]] = set()
            out: list[dict[str, Any]] = []
            for row in rows:
                key = ((row.city_slug or row.city or "").lower(), int(row.radius_km or 0))
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "search_run_id": row.search_run_id,
                    "city": row.city,
                    "city_slug": row.city_slug,
                    "radius_km": row.radius_km,
                    "filters": _loads(row.filters_json, {}) or {},
                    "advert_count": row.advert_count,
                    "started_at": _iso(row.started_at),
                    "finished_at": _iso(row.finished_at),
                })
                if len(out) >= limit:
                    break
            return out

    @staticmethod
    def _ensure_listing_from_search(
        db: Session,
        profile: SearchProfile,
        now: datetime,
    ) -> Listing:
        listing = db.scalar(select(Listing).where(
            Listing.source == "LADIES",
            Listing.external_listing_id == str(profile.advert_id),
        ))
        if listing is None:
            person = Person(
                display_name=profile.name or None,
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(person)
            db.flush()
            db.add(PersonUserState(person_id=person.person_id))
            listing = Listing(
                person_id=person.person_id,
                source="LADIES",
                external_listing_id=str(profile.advert_id),
                current_url=profile.profile_url,
                category=profile.category,
                first_seen_at=now,
                last_seen_at=now,
                current_status="ACTIVE",
                match_decision="UNRESOLVED",
            )
            db.add(listing)
            db.flush()
        else:
            listing.last_seen_at = now
            listing.current_url = profile.profile_url or listing.current_url
            listing.category = profile.category or listing.category
            listing.current_status = "ACTIVE"
            listing.ended_at = None
            person = db.get(Person, listing.person_id)
            if person:
                person.last_seen_at = now
                person.updated_at = now
                if profile.name:
                    person.display_name = profile.name
        return listing

    @staticmethod
    def _search_payload(db: Session, listing: Listing, profile: SearchProfile) -> dict[str, Any]:
        state = db.get(PersonUserState, listing.person_id)
        listing_count = db.scalar(
            select(func.count(Listing.listing_id)).where(Listing.person_id == listing.person_id)
        ) or 0
        first = db.scalar(
            select(func.min(Listing.first_seen_at)).where(Listing.person_id == listing.person_id)
        )
        recent_change = db.scalar(
            select(ChangeEvent)
            .where(ChangeEvent.person_id == listing.person_id)
            .order_by(desc(ChangeEvent.created_at))
            .limit(1)
        )
        recent_location_change = db.scalar(
            select(ChangeEvent)
            .where(
                ChangeEvent.person_id == listing.person_id,
                ChangeEvent.field_key == "city",
            )
            .order_by(desc(ChangeEvent.created_at))
            .limit(1)
        )
        classification = SearchRepository._business_profile_payload(db, listing)
        return {
            "advert_id": listing.external_listing_id,
            "listing_id": listing.listing_id,
            "person_id": listing.person_id,
            "name": profile.name,
            "city": profile.city,
            "category": profile.category,
            "profile_url": profile.profile_url,
            "thumbnail_url": profile.thumbnail_url,
            "has_video": profile.has_video,
            "is_premium": profile.is_premium,
            "has_ai_content": profile.has_ai_content,
            "markers": profile.markers,
            "preference": state.preference if state else "NONE",
            "favorite": bool(state.favorite) if state else False,
            "known_listing_count": int(listing_count),
            "reappear_count": max(0, int(listing_count) - 1),
            "person_first_seen_at": _iso(first),
            "listing_published_at": _iso(listing.published_at),
            "listing_first_seen_at": _iso(listing.first_seen_at),
            "listing_last_seen_at": _iso(listing.last_seen_at),
            "current_status": listing.current_status,
            "match_decision": listing.match_decision,
            "recent_change": _change_payload(recent_change),
            "recent_location_change": _change_payload(recent_location_change),
            **classification,
        }
