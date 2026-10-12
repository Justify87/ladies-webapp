from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import orjson
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from ladies_v9.database import Database
from ladies_v9.models import (
    ChangeEvent,
    Encounter,
    EncounterService,
    Listing,
    ListingObservation,
    ListingSnapshot,
    MediaAsset,
    Person,
    PersonIdentifier,
    PersonUserState,
    PriceItem,
    PriceList,
    SearchResult,
    SnapshotMedia,
    SnapshotValue,
    SourceProfile,
    VisualAnalysis,
)
from .history import HistoryRepository


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


class PersonReadRepository:
    """Person/listing read models kept separate from identity write operations."""

    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _state_payload(state: PersonUserState | None) -> dict[str, Any]:
        return {
            "preference": state.preference if state else "NONE",
            "favorite": bool(state.favorite) if state else False,
            "repeat_interest": state.repeat_interest if state else "UNKNOWN",
            "private_note": state.private_note if state else "",
            "updated_at": _iso(state.updated_at) if state else None,
        }

    @staticmethod
    def _price_list_payload(db: Session, row: PriceList) -> dict[str, Any]:
        items = db.scalars(
            select(PriceItem)
            .where(PriceItem.price_list_id == row.price_list_id)
            .order_by(PriceItem.price_item_id)
        ).all()
        return {
            "price_list_id": row.price_list_id,
            "person_id": row.person_id,
            "listing_id": row.listing_id,
            "observed_at": _iso(row.observed_at),
            "source": row.source,
            "currency": row.currency,
            "note": row.note,
            "items": [
                {
                    "price_item_id": item.price_item_id,
                    "item_type": item.item_type,
                    "label": item.label,
                    "duration_minutes": item.duration_minutes,
                    "service": item.service,
                    "amount_cents": item.amount_cents,
                    "amount_eur": item.amount_cents / 100,
                    "note": item.note,
                }
                for item in items
            ],
        }

    @staticmethod
    def _encounter_payload(db: Session, row: Encounter) -> dict[str, Any]:
        items = db.scalars(
            select(EncounterService)
            .where(EncounterService.encounter_id == row.encounter_id)
            .order_by(EncounterService.encounter_service_id)
        ).all()
        return {
            "encounter_id": row.encounter_id,
            "person_id": row.person_id,
            "listing_id": row.listing_id,
            "occurred_at": _iso(row.occurred_at),
            "duration_minutes": row.duration_minutes,
            "base_price_eur": row.base_price_cents / 100 if row.base_price_cents is not None else None,
            "total_price_eur": row.total_price_cents / 100 if row.total_price_cents is not None else None,
            "overall_rating": row.overall_rating,
            "sympathy_rating": row.sympathy_rating,
            "photo_status": row.photo_status,
            "service_accuracy": row.service_accuracy,
            "hygiene": row.hygiene,
            "kissing": row.kissing,
            "negotiable": row.negotiable,
            "note": row.note,
            "services": [
                {
                    "service_name": item.service_name,
                    "amount_eur": item.amount_cents / 100 if item.amount_cents is not None else None,
                    "performed": item.performed,
                    "note": item.note,
                }
                for item in items
            ],
        }

    @staticmethod
    def _feature_key(kind: str, label: str | None, value: str | None) -> tuple[str, str, str]:
        normalize = lambda text: " ".join(
            str(text or "").casefold().replace("ß", "ss").split()
        )
        return str(kind or ""), normalize(label), normalize(value)

    def _person_analytics(
        self,
        db: Session,
        person: Person,
        listings: list[Listing],
        latest_snapshot: ListingSnapshot | None,
    ) -> dict[str, Any]:
        listing_ids = [row.listing_id for row in listings]
        snapshots = (
            db.scalars(
                select(ListingSnapshot).where(ListingSnapshot.listing_id.in_(listing_ids))
            ).all()
            if listing_ids else []
        )
        snapshot_ids = [row.snapshot_id for row in snapshots]
        snapshot_listing = {row.snapshot_id: row.listing_id for row in snapshots}
        media_rows = (
            db.execute(
                select(SnapshotMedia, MediaAsset)
                .outerjoin(MediaAsset, MediaAsset.media_asset_id == SnapshotMedia.media_asset_id)
                .where(SnapshotMedia.snapshot_id.in_(snapshot_ids))
            ).all()
            if snapshot_ids else []
        )
        assets: dict[int, tuple[SnapshotMedia, MediaAsset]] = {}
        asset_listings: dict[int, set[int]] = defaultdict(set)
        for snapshot_media, asset in media_rows:
            if asset and snapshot_media.media_asset_id:
                assets.setdefault(asset.media_asset_id, (snapshot_media, asset))
                asset_listings[asset.media_asset_id].add(
                    snapshot_listing.get(snapshot_media.snapshot_id, 0)
                )
        image_ids = [asset_id for asset_id, (_, asset) in assets.items() if asset.kind == "IMAGE"]
        video_ids = [asset_id for asset_id, (_, asset) in assets.items() if asset.kind == "VIDEO"]
        analyses = (
            db.scalars(
                select(VisualAnalysis)
                .where(VisualAnalysis.media_asset_id.in_(image_ids))
                .order_by(desc(VisualAnalysis.checked_at))
            ).all()
            if image_ids else []
        )
        latest_analysis: dict[int, VisualAnalysis] = {}
        for row in analyses:
            latest_analysis.setdefault(row.media_asset_id, row)
        flagged = sum(1 for row in latest_analysis.values() if int(row.score or 0) >= 12)
        high = sum(1 for row in latest_analysis.values() if int(row.score or 0) >= 30)
        multi_face = 0
        for row in latest_analysis.values():
            diagnostics = _loads(row.diagnostics_json, {}) or {}
            if int(diagnostics.get("faces") or 0) > 1:
                multi_face += 1
        shared = 0
        if image_ids:
            shared = len(set(db.scalars(
                select(SnapshotMedia.media_asset_id)
                .join(ListingSnapshot, ListingSnapshot.snapshot_id == SnapshotMedia.snapshot_id)
                .join(Listing, Listing.listing_id == ListingSnapshot.listing_id)
                .where(
                    SnapshotMedia.media_asset_id.in_(image_ids),
                    Listing.person_id != person.person_id,
                )
                .distinct()
            ).all()))
        cities = sorted({
            str(row.city).strip() for row in snapshots if row.city and str(row.city).strip()
        })
        names = sorted({
            str(row.name).strip() for row in snapshots if row.name and str(row.name).strip()
        })
        changes = db.scalars(
            select(ChangeEvent).where(ChangeEvent.person_id == person.person_id)
        ).all()
        by_field: dict[str, int] = defaultdict(int)
        for row in changes:
            by_field[row.field_key or row.event_type or "other"] += 1

        target_tokens: dict[tuple[str, str, str], str] = {}
        if latest_snapshot:
            values = db.scalars(
                select(SnapshotValue).where(
                    SnapshotValue.snapshot_id == latest_snapshot.snapshot_id,
                    SnapshotValue.kind.in_(["TRAIT", "SERVICE"]),
                )
            ).all()
            for value in values:
                key = self._feature_key(value.kind, value.label or value.group_name, value.value)
                target_tokens[key] = (
                    f"{value.label or value.group_name}: {value.value}"
                    if value.kind == "TRAIT" else str(value.value)
                )
        pref_rows = db.execute(
            select(
                PersonUserState.person_id,
                PersonUserState.preference,
                SnapshotValue.kind,
                SnapshotValue.label,
                SnapshotValue.group_name,
                SnapshotValue.value,
            )
            .join(Listing, Listing.person_id == PersonUserState.person_id)
            .join(SnapshotValue, SnapshotValue.snapshot_id == Listing.current_snapshot_id)
            .where(
                PersonUserState.preference.in_(["LIKE", "DISLIKE"]),
                PersonUserState.person_id != person.person_id,
                Listing.current_snapshot_id.is_not(None),
                SnapshotValue.kind.in_(["TRAIT", "SERVICE"]),
            )
        ).all()
        per_person: dict[int, tuple[str, set[tuple[str, str, str]]]] = {}
        for person_id, preference, kind, label, group, value in pref_rows:
            bucket = per_person.setdefault(int(person_id), (preference, set()))[1]
            bucket.add(self._feature_key(kind, label or group, value))
        like_samples = sum(1 for preference, _ in per_person.values() if preference == "LIKE")
        dislike_samples = sum(1 for preference, _ in per_person.values() if preference == "DISLIKE")
        evidence = []
        for key, label in target_tokens.items():
            like_count = sum(
                1 for preference, tokens in per_person.values()
                if preference == "LIKE" and key in tokens
            )
            dislike_count = sum(
                1 for preference, tokens in per_person.values()
                if preference == "DISLIKE" and key in tokens
            )
            if like_count + dislike_count:
                signal = (like_count - dislike_count) / (like_count + dislike_count)
                evidence.append((signal, like_count + dislike_count, label, like_count, dislike_count))
        if like_samples + dislike_samples >= 3 and evidence:
            weighted = sum(
                signal * min(3, count) for signal, count, *_ in evidence
            ) / max(1, sum(min(3, count) for _, count, *_ in evidence))
            affinity_score = max(0, min(100, round(50 + weighted * 38)))
            positive = [
                {"label": label, "likes": likes, "dislikes": dislikes}
                for signal, count, label, likes, dislikes in sorted(
                    evidence, key=lambda row: (row[0], row[1]), reverse=True
                )
                if signal > 0
            ][:5]
            negative = [
                {"label": label, "likes": likes, "dislikes": dislikes}
                for signal, count, label, likes, dislikes in sorted(
                    evidence, key=lambda row: (row[0], -row[1])
                )
                if signal < 0
            ][:4]
            affinity = {
                "score": affinity_score,
                "like_samples": like_samples,
                "dislike_samples": dislike_samples,
                "positive": positive,
                "negative": negative,
            }
        else:
            affinity = {
                "score": None,
                "like_samples": like_samples,
                "dislike_samples": dislike_samples,
                "positive": [],
                "negative": [],
                "note": "Mindestens drei bewertete Personen mit vergleichbaren Merkmalen nötig",
            }
        return {
            "media": {
                "unique_media": len(assets),
                "image_count": len(image_ids),
                "video_count": len(video_ids),
                "analyzed_images": len(latest_analysis),
                "flagged_images": flagged,
                "high_flag_images": high,
                "multi_face_images": multi_face,
                "reused_across_listings": sum(
                    1 for ids in asset_listings.values()
                    if len({value for value in ids if value}) > 1
                ),
                "shared_with_other_people": shared,
            },
            "consistency": {
                "city_count": len(cities),
                "cities": cities,
                "name_count": len(names),
                "names": names,
                "changes_by_field": dict(by_field),
            },
            "affinity": affinity,
        }

    def person_detail(self, person_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            person = db.get(Person, int(person_id))
            if person is None:
                raise KeyError(person_id)
            state = db.get(PersonUserState, person.person_id)
            listings = db.scalars(
                select(Listing)
                .where(Listing.person_id == person.person_id)
                .order_by(desc(Listing.last_seen_at))
            ).all()
            source_profiles = db.scalars(
                select(SourceProfile)
                .where(SourceProfile.person_id == person.person_id)
                .order_by(SourceProfile.source_profile_id)
            ).all()
            identifiers = db.scalars(
                select(PersonIdentifier)
                .where(PersonIdentifier.person_id == person.person_id)
                .order_by(PersonIdentifier.kind, PersonIdentifier.first_seen_at)
            ).all()
            changes = db.scalars(
                select(ChangeEvent)
                .where(ChangeEvent.person_id == person.person_id)
                .order_by(desc(ChangeEvent.created_at))
                .limit(100)
            ).all()
            prices = db.scalars(
                select(PriceList)
                .where(PriceList.person_id == person.person_id)
                .order_by(desc(PriceList.observed_at))
            ).all()
            encounters = db.scalars(
                select(Encounter)
                .where(Encounter.person_id == person.person_id)
                .order_by(desc(Encounter.occurred_at))
            ).all()
            latest_snapshot = None
            for listing in listings:
                if listing.current_snapshot_id:
                    snapshot = db.get(ListingSnapshot, listing.current_snapshot_id)
                    if snapshot and (
                        latest_snapshot is None or snapshot.captured_at > latest_snapshot.captured_at
                    ):
                        latest_snapshot = snapshot
            return {
                "person": {
                    "person_id": person.person_id,
                    "display_name": person.display_name,
                    "first_seen_at": _iso(person.first_seen_at),
                    "last_seen_at": _iso(person.last_seen_at),
                    "listing_count": len(listings),
                    "reappear_count": max(0, len(listings) - 1),
                },
                "user_state": self._state_payload(state),
                "source_profiles": [
                    {
                        "source_profile_id": row.source_profile_id,
                        "source": row.source,
                        "external_profile_id": row.external_profile_id,
                        "canonical_id": row.canonical_id,
                        "first_seen_at": _iso(row.first_seen_at),
                        "last_seen_at": _iso(row.last_seen_at),
                    }
                    for row in source_profiles
                ],
                "identifiers": [
                    {
                        "kind": row.kind,
                        "value": row.normalized_value,
                        "first_seen_at": _iso(row.first_seen_at),
                        "last_seen_at": _iso(row.last_seen_at),
                    }
                    for row in identifiers if row.kind != "IMAGE_DHASH"
                ],
                "listings": [HistoryRepository._listing_payload(db, row) for row in listings],
                "latest_snapshot": (
                    HistoryRepository._snapshot_payload(db, latest_snapshot)
                    if latest_snapshot else None
                ),
                "timeline": [_change_payload(row) for row in changes],
                "price_lists": [self._price_list_payload(db, row) for row in prices],
                "encounters": [self._encounter_payload(db, row) for row in encounters],
                "analytics": self._person_analytics(db, person, listings, latest_snapshot),
            }

    def listing_detail(self, advert_id: str) -> dict[str, Any]:
        with self.database.session() as db:
            listing = db.scalar(
                select(Listing).where(Listing.external_listing_id == str(advert_id))
            )
            if listing is None:
                raise KeyError(advert_id)
            snapshot = (
                db.get(ListingSnapshot, listing.current_snapshot_id)
                if listing.current_snapshot_id else None
            )
            observations = db.scalars(
                select(ListingObservation)
                .where(ListingObservation.listing_id == listing.listing_id)
                .order_by(desc(ListingObservation.observed_at))
                .limit(100)
            ).all()
            snapshots = db.scalars(
                select(ListingSnapshot)
                .where(ListingSnapshot.listing_id == listing.listing_id)
                .order_by(desc(ListingSnapshot.captured_at))
            ).all()
            return {
                "listing": HistoryRepository._listing_payload(db, listing),
                "profile": (
                    HistoryRepository._snapshot_payload(db, snapshot) if snapshot else None
                ),
                "snapshots": [
                    {
                        "snapshot_id": row.snapshot_id,
                        "captured_at": _iso(row.captured_at),
                        "fingerprint": row.fingerprint,
                    }
                    for row in snapshots
                ],
                "observations": [
                    {
                        "source": row.source,
                        "status": row.status,
                        "observed_at": _iso(row.observed_at),
                        "http_status": row.http_status,
                        "snapshot_id": row.snapshot_id,
                    }
                    for row in observations
                ],
                "person": self.person_detail(listing.person_id),
            }

    def lists(self, tab: str = "LIKE") -> list[dict[str, Any]]:
        tab = str(tab).upper()
        with self.database.session() as db:
            query = select(Person, PersonUserState).join(
                PersonUserState, PersonUserState.person_id == Person.person_id
            )
            if tab == "FAVORITE":
                query = query.where(PersonUserState.favorite.is_(True))
            elif tab in {"LIKE", "DISLIKE"}:
                query = query.where(PersonUserState.preference == tab)
            else:
                query = query.where(or_(
                    PersonUserState.preference != "NONE",
                    PersonUserState.favorite.is_(True),
                ))
            rows = db.execute(query.order_by(desc(PersonUserState.updated_at))).all()
            out = []
            for person, state in rows:
                listing = db.scalar(
                    select(Listing)
                    .where(Listing.person_id == person.person_id)
                    .order_by(desc(Listing.last_seen_at))
                    .limit(1)
                )
                snapshot = (
                    db.get(ListingSnapshot, listing.current_snapshot_id)
                    if listing and listing.current_snapshot_id else None
                )
                latest_search = (
                    db.scalar(
                        select(SearchResult)
                        .where(SearchResult.listing_id == listing.listing_id)
                        .order_by(desc(SearchResult.observed_at))
                        .limit(1)
                    ) if listing else None
                )
                person_search = db.scalar(
                    select(SearchResult)
                    .join(Listing, SearchResult.listing_id == Listing.listing_id)
                    .where(
                        Listing.person_id == person.person_id,
                        SearchResult.city.is_not(None),
                    )
                    .order_by(desc(SearchResult.observed_at))
                    .limit(1)
                )
                fallback_snapshot = db.scalar(
                    select(ListingSnapshot)
                    .join(Listing, ListingSnapshot.listing_id == Listing.listing_id)
                    .where(
                        Listing.person_id == person.person_id,
                        ListingSnapshot.city.is_not(None),
                    )
                    .order_by(desc(ListingSnapshot.captured_at))
                    .limit(1)
                )
                thumbnail = None
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
                    if media and media.media_asset_id:
                        asset = db.get(MediaAsset, media.media_asset_id)
                        thumbnail = f"/archive/{asset.local_path}" if asset else media.remote_url
                    elif media:
                        thumbnail = media.remote_url
                if thumbnail is None and latest_search:
                    thumbnail = latest_search.thumbnail_url
                if thumbnail is None and person_search:
                    thumbnail = person_search.thumbnail_url
                listing_count = db.scalar(
                    select(func.count(Listing.listing_id)).where(
                        Listing.person_id == person.person_id
                    )
                ) or 0
                recent_change = db.scalar(
                    select(ChangeEvent)
                    .where(ChangeEvent.person_id == person.person_id)
                    .order_by(desc(ChangeEvent.created_at))
                    .limit(1)
                )
                out.append({
                    "person_id": person.person_id,
                    "name": (
                        (snapshot.name if snapshot and snapshot.name else None)
                        or (latest_search.name if latest_search and latest_search.name else None)
                        or (person_search.name if person_search and person_search.name else None)
                        or person.display_name
                    ),
                    "city": (
                        (snapshot.city if snapshot and snapshot.city else None)
                        or (fallback_snapshot.city if fallback_snapshot and fallback_snapshot.city else None)
                        or (latest_search.city if latest_search and latest_search.city else None)
                        or (person_search.city if person_search and person_search.city else None)
                    ),
                    "thumbnail_url": thumbnail,
                    "preference": state.preference,
                    "favorite": state.favorite,
                    "listing_count": int(listing_count),
                    "reappear_count": max(0, int(listing_count) - 1),
                    "last_seen_at": _iso(person.last_seen_at),
                    "latest_advert_id": listing.external_listing_id if listing else None,
                    "status": listing.current_status if listing else "UNKNOWN",
                    "status_checked_at": _iso(listing.status_checked_at) if listing else None,
                    "recent_change": _change_payload(recent_change),
                })
            return out

    def _profile_payload(
        self,
        db: Session,
        listing: Listing,
        snapshot: ListingSnapshot,
    ) -> dict[str, Any]:
        person = db.get(Person, listing.person_id)
        state = db.get(PersonUserState, listing.person_id)
        count = db.scalar(
            select(func.count(Listing.listing_id)).where(Listing.person_id == listing.person_id)
        ) or 0
        return {
            "listing": HistoryRepository._listing_payload(db, listing),
            "profile": HistoryRepository._snapshot_payload(db, snapshot),
            "identity": {
                "person_id": listing.person_id,
                "display_name": person.display_name if person else snapshot.name,
                "preference": state.preference if state else "NONE",
                "favorite": state.favorite if state else False,
                "known_listing_count": int(count),
                "reappear_count": max(0, int(count) - 1),
                "match_decision": listing.match_decision,
                "match_reason": listing.match_reason,
                "candidate_person_id": listing.candidate_person_id,
                "best_distance": listing.match_best_distance,
                "image_matches": listing.match_image_count,
                "strong_image_matches": listing.match_strong_image_count,
                "phone_match": listing.match_phone,
                "source_profile_match": listing.match_source_profile,
                "person_first_seen_at": _iso(person.first_seen_at) if person else None,
                "person_last_seen_at": _iso(person.last_seen_at) if person else None,
            },
        }
