from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

import orjson
from sqlalchemy import and_, delete, desc, func, or_, select, update
from sqlalchemy.orm import Session

from .database import Database
from .domain import ProfileData, SearchProfile
from .identity import MatchResult, hash_from_hex, match_identity, normalize_phone_number
from .media import ArchiveManager, ArchivedMedia
from .models import (
    AppSetting,
    ChangeEvent,
    Encounter,
    EncounterService,
    IdentityEvidence,
    IdentityEvent,
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
    SearchRun,
    SnapshotMedia,
    SnapshotValue,
    SourceProfile,
    VisualAnalysis,
    utcnow,
)
from .utils import parse_iso_datetime

LOGGER = logging.getLogger("ladies_explorer.repository")


def dumps(value: Any) -> str:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS).decode("utf-8")


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return orjson.loads(value)
    except Exception:
        return default


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


DEFAULT_SETTINGS = {
    "archive_videos": False,
    "archive_raw_html": True,
    "visual_check_enabled": True,
    "show_description_changes": False,
    "replay_likes": True,
    # V9.1 presentation/runtime preferences. These only influence the frontend;
    # the V9 person/listing/snapshot model remains unchanged.
    "autoplay_videos": False,
    "reduced_motion": False,
    # Optional user-defined defaults for the search/filter UI. None means the
    # built-in mobile defaults are used.
    "default_filters": None,
}


class Repository:
    def __init__(self, database: Database, archive: ArchiveManager):
        self.database = database
        self.archive = archive
        self.database.create_schema()
        self._ensure_default_settings()
        LOGGER.info("Repository initialized")

    def _ensure_default_settings(self) -> None:
        with self.database.session() as db:
            for key, value in DEFAULT_SETTINGS.items():
                if db.get(AppSetting, key) is None:
                    db.add(AppSetting(key=key, value_json=dumps(value)))

    def settings(self) -> dict[str, Any]:
        with self.database.session() as db:
            rows = db.scalars(select(AppSetting)).all()
            out = dict(DEFAULT_SETTINGS)
            out.update({row.key: loads(row.value_json) for row in rows})
            return out

    def set_setting(self, key: str, value: Any) -> dict[str, Any]:
        LOGGER.info("Setting update key=%s", key)
        if key not in DEFAULT_SETTINGS:
            raise ValueError(f"Unbekannte Einstellung: {key}")
        with self.database.session() as db:
            row = db.get(AppSetting, key)
            if row is None:
                row = AppSetting(key=key, value_json=dumps(value))
                db.add(row)
            else:
                row.value_json = dumps(value)
                row.updated_at = utcnow()
        return self.settings()

    def recent_searches(self, limit: int = 8) -> list[dict[str, Any]]:
        """Return recent, de-duplicated search presets for the V9.1 home screen."""
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
                filters = loads(row.filters_json, {}) or {}
                out.append({
                    "search_run_id": row.search_run_id,
                    "city": row.city,
                    "city_slug": row.city_slug,
                    "radius_km": row.radius_km,
                    "filters": filters,
                    "advert_count": row.advert_count,
                    "started_at": iso(row.started_at),
                    "finished_at": iso(row.finished_at),
                })
                if len(out) >= limit:
                    break
            return out

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
        """Small local-storage summary used by the settings screen."""
        db_path = self.database.path
        db_bytes = 0
        try:
            db_bytes = db_path.stat().st_size
        except OSError:
            pass
        with self.database.session() as db:
            image_count = db.scalar(select(func.count(MediaAsset.media_asset_id)).where(MediaAsset.kind == "IMAGE")) or 0
            video_count = db.scalar(select(func.count(MediaAsset.media_asset_id)).where(MediaAsset.kind == "VIDEO")) or 0
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

    def archive_items(self, kind: str | None = None, limit: int = 80) -> list[dict[str, Any]]:
        """Return locally known media assets without changing archival semantics."""
        limit = max(1, min(300, int(limit)))
        with self.database.session() as db:
            q = select(MediaAsset)
            if kind:
                q = q.where(MediaAsset.kind == str(kind).upper())
            rows = db.scalars(q.order_by(desc(MediaAsset.last_seen_at)).limit(limit)).all()
            return [{
                "media_asset_id": x.media_asset_id,
                "kind": x.kind,
                "mime_type": x.mime_type,
                "byte_size": x.byte_size,
                "width": x.width,
                "height": x.height,
                "first_seen_at": iso(x.first_seen_at),
                "last_seen_at": iso(x.last_seen_at),
                "local_url": f"/archive/{x.local_path}" if x.local_path else None,
            } for x in rows]

    def clear_visual_analyses(self) -> int:
        with self.database.session() as db:
            count = db.scalar(select(func.count(VisualAnalysis.visual_analysis_id))) or 0
            db.execute(delete(VisualAnalysis))
            return int(count)

    # ------------------------------------------------------------------
    # Search history
    # ------------------------------------------------------------------
    def create_search_run(self, *, city: str, city_slug: str, radius_km: int, filters: dict[str, Any]) -> int:
        with self.database.session() as db:
            row = SearchRun(city=city, city_slug=city_slug, radius_km=radius_km, filters_json=dumps(filters))
            db.add(row)
            db.flush()
            return row.search_run_id

    def finish_search_run(self, search_run_id: int, profiles: list[SearchProfile], meta: dict[str, Any]) -> list[dict[str, Any]]:
        now = utcnow()
        LOGGER.info("Persist search run_id=%s profiles=%s", search_run_id, len(profiles))
        result_payload: list[dict[str, Any]] = []
        with self.database.session() as db:
            run = db.get(SearchRun, search_run_id)
            if run is None:
                raise ValueError("Search run nicht gefunden")
            run.finished_at = now
            run.advert_count = meta.get("server_advert_count") if meta.get("server_advert_count") is not None else len(profiles)
            try:
                run.distance_searched = int(meta.get("distance_searched")) if meta.get("distance_searched") is not None else run.radius_km
            except (TypeError, ValueError):
                run.distance_searched = run.radius_km
            run.metadata_json = dumps(meta)

            for position, profile in enumerate(profiles, start=1):
                listing = self._ensure_listing_from_search(db, profile, now)
                sr = SearchResult(
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
                    markers_json=dumps(profile.markers),
                )
                db.add(sr)
                db.add(ListingObservation(
                    listing_id=listing.listing_id,
                    observed_at=now,
                    source="SEARCH",
                    status="SEEN",
                    search_run_id=run.search_run_id,
                    details_json=dumps({"position": position, "name": profile.name, "city": profile.city}),
                ))
                result_payload.append(self._search_payload(db, listing, profile))
        return result_payload

    def _ensure_listing_from_search(self, db: Session, profile: SearchProfile, now: datetime) -> Listing:
        listing = db.scalar(select(Listing).where(
            Listing.source == "LADIES", Listing.external_listing_id == str(profile.advert_id)
        ))
        if listing is None:
            person = Person(display_name=profile.name or None, first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now)
            db.add(person); db.flush()
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
            db.add(listing); db.flush()
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

    def _search_payload(self, db: Session, listing: Listing, profile: SearchProfile) -> dict[str, Any]:
        state = db.get(PersonUserState, listing.person_id)
        listing_count = db.scalar(select(func.count(Listing.listing_id)).where(Listing.person_id == listing.person_id)) or 0
        first = db.scalar(select(func.min(Listing.first_seen_at)).where(Listing.person_id == listing.person_id))
        recent_change = db.scalar(
            select(ChangeEvent).where(ChangeEvent.person_id == listing.person_id)
            .order_by(desc(ChangeEvent.created_at)).limit(1)
        )
        recent_location_change = db.scalar(
            select(ChangeEvent).where(ChangeEvent.person_id == listing.person_id, ChangeEvent.field_key == "city")
            .order_by(desc(ChangeEvent.created_at)).limit(1)
        )
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
            "person_first_seen_at": iso(first),
            "listing_published_at": iso(listing.published_at),
            "listing_first_seen_at": iso(listing.first_seen_at),
            "listing_last_seen_at": iso(listing.last_seen_at),
            "current_status": listing.current_status,
            "match_decision": listing.match_decision,
            "recent_change": self._change_payload(recent_change) if recent_change else None,
            "recent_location_change": self._change_payload(recent_location_change) if recent_location_change else None,
        }

    # ------------------------------------------------------------------
    # Identity / snapshot ingestion
    # ------------------------------------------------------------------
    def ingest_profile(self, *, external_listing_id: str, profile: ProfileData, raw_html: str, http_session) -> dict[str, Any]:
        LOGGER.info("Ingest profile advert_id=%s name=%s media=%s", external_listing_id, profile.name, len(profile.media))
        settings = self.settings()
        now = utcnow()
        with self.database.session() as db:
            listing = db.scalar(select(Listing).where(Listing.source == "LADIES", Listing.external_listing_id == str(external_listing_id)))
            if listing is None:
                person = Person(display_name=profile.name or None)
                db.add(person); db.flush(); db.add(PersonUserState(person_id=person.person_id))
                listing = Listing(
                    person_id=person.person_id, source="LADIES", external_listing_id=str(external_listing_id),
                    current_url=profile.source_url, category=profile.category, current_status="ACTIVE",
                    match_decision="UNRESOLVED",
                )
                db.add(listing); db.flush()

            archived_media: list[tuple[Any, ArchivedMedia]] = []
            image_hashes: list[int] = []
            for ref in sorted(profile.media, key=lambda m: (m.position, m.kind)):
                should_download = ref.kind == "IMAGE" or bool(settings.get("archive_videos"))
                try:
                    archived = self.archive.ensure_remote_media(db, http_session, ref, download=should_download)
                except Exception:
                    # Keep the remote reference even when media is temporarily unreachable.
                    archived = self.archive.ensure_remote_media(db, http_session, ref, download=False)
                archived_media.append((ref, archived))
                if archived.asset and archived.asset.dhash and ref.kind == "IMAGE":
                    try:
                        value = hash_from_hex(archived.asset.dhash)
                        if value not in image_hashes:
                            image_hashes.append(value)
                    except ValueError:
                        pass

            normalized_phone = normalize_phone_number(profile.phone_display or profile.tel_url or profile.whatsapp_url)
            resolution = self._resolve_identity(
                db,
                listing=listing,
                source_profile_external_id=profile.source_profile_id,
                normalized_phone=normalized_phone,
                image_hashes=image_hashes,
            )
            target_person_id = resolution[0]
            result = resolution[1]
            candidate_person_id = resolution[2]
            if target_person_id != listing.person_id and result.decision == "MATCH":
                old_person = listing.person_id
                self._merge_persons_in_session(db, source_person_id=old_person, target_person_id=target_person_id, action="AUTO_MATCH")
                listing = db.get(Listing, listing.listing_id)

            listing.match_decision = result.decision if result.decision != "NO_MATCH" else (listing.match_decision if listing.match_decision != "UNRESOLVED" else "NO_MATCH")
            listing.candidate_person_id = candidate_person_id if result.decision != "MATCH" else None
            listing.match_reason = result.reason
            listing.match_best_distance = result.best_distance
            listing.match_image_count = result.candidate_image_matches
            listing.match_strong_image_count = result.strong_image_matches
            listing.match_phone = result.phone_match
            listing.match_source_profile = result.source_profile_match
            listing.current_url = profile.source_url
            listing.category = profile.category
            listing.published_at = parse_iso_datetime(profile.published_at)
            listing.last_seen_at = now
            listing.current_status = "ACTIVE"
            listing.status_checked_at = now
            listing.ended_at = None

            person = db.get(Person, listing.person_id)
            if person:
                person.display_name = profile.name or person.display_name
                person.last_seen_at = now
                person.updated_at = now

            source_profile = self._upsert_source_profile(db, listing.person_id, profile, allow_reassign=listing.match_decision != "MANUAL_SPLIT")
            if source_profile:
                listing.source_profile_id = source_profile.source_profile_id

            self._touch_identifier(db, listing.person_id, listing.listing_id, "PHONE", normalized_phone, profile.phone_display)
            self._touch_identifier(db, listing.person_id, listing.listing_id, "SOURCE_PROFILE", profile.source_profile_id, profile.source_profile_id)
            for ref, archived in archived_media:
                if archived.asset and archived.asset.dhash:
                    self._touch_identifier(db, listing.person_id, listing.listing_id, "IMAGE_DHASH", archived.asset.dhash, ref.url)

            fingerprint_payload = self._fingerprint_payload(profile, normalized_phone, archived_media)
            fingerprint = hashlib.sha256(orjson.dumps(fingerprint_payload, option=orjson.OPT_SORT_KEYS)).hexdigest()
            previous = db.get(ListingSnapshot, listing.current_snapshot_id) if listing.current_snapshot_id else None
            existing = db.scalar(select(ListingSnapshot).where(
                ListingSnapshot.listing_id == listing.listing_id,
                ListingSnapshot.fingerprint == fingerprint,
            ))
            if existing is not None:
                listing.current_snapshot_id = existing.snapshot_id
                db.add(ListingObservation(
                    listing_id=listing.listing_id, observed_at=now, source="PROFILE", status="ACTIVE",
                    http_status=200, snapshot_id=existing.snapshot_id,
                    details_json=dumps({"snapshot_changed": False}),
                ))
                return self._profile_payload(db, listing, existing)

            raw_sha = raw_path = None
            if settings.get("archive_raw_html", True):
                raw_sha, raw_path = self.archive.archive_html(raw_html)
            snapshot = ListingSnapshot(
                listing_id=listing.listing_id,
                captured_at=now,
                fingerprint=fingerprint,
                source_url=profile.source_url,
                name=profile.name,
                city=profile.city,
                postal_code=profile.postal_code,
                hidden_location=profile.hidden_location,
                category=profile.category,
                description=profile.description,
                published_at=parse_iso_datetime(profile.published_at),
                external_profile_id=profile.source_profile_id,
                canonical_id=profile.canonical_id,
                phone_display=profile.phone_display,
                normalized_phone=normalized_phone,
                whatsapp_url=profile.whatsapp_url,
                sms_url=profile.sms_url,
                tel_url=profile.tel_url,
                traits_json=dumps(profile.traits),
                services_json=dumps(profile.services),
                opening_times_json=dumps(profile.opening_times),
                auxiliary_json=dumps({"provenance": profile.provenance, "auxiliary": profile.auxiliary}),
                raw_html_sha256=raw_sha,
                raw_html_path=raw_path,
            )
            db.add(snapshot); db.flush()
            self._add_snapshot_values(db, snapshot, profile)
            self._add_snapshot_media(db, snapshot, archived_media)
            listing.current_snapshot_id = snapshot.snapshot_id

            if previous is None:
                db.add(ChangeEvent(
                    person_id=listing.person_id, listing_id=listing.listing_id,
                    to_snapshot_id=snapshot.snapshot_id,
                    event_type="LISTING_FIRST_SNAPSHOT", importance="MEDIUM",
                    new_json=dumps({"advert_id": listing.external_listing_id, "name": profile.name}),
                ))
            else:
                self._create_change_events(db, listing, previous, snapshot)

            if result.decision == "MATCH" and result.reason != "Externe Anzeigen-ID ist bereits bekannt.":
                db.add(ChangeEvent(
                    person_id=listing.person_id, listing_id=listing.listing_id,
                    to_snapshot_id=snapshot.snapshot_id,
                    event_type="NEW_LISTING_FOR_PERSON", importance="HIGH",
                    new_json=dumps({"advert_id": listing.external_listing_id, "reason": result.reason}),
                ))

            self._record_identity_evidence(db, listing, result, profile.source_profile_id, normalized_phone)
            db.add(ListingObservation(
                listing_id=listing.listing_id, observed_at=now, source="PROFILE", status="ACTIVE",
                http_status=200, snapshot_id=snapshot.snapshot_id,
                details_json=dumps({"snapshot_changed": True, "fingerprint": fingerprint}),
            ))
            return self._profile_payload(db, listing, snapshot)

    def _resolve_identity(
        self, db: Session, *, listing: Listing,
        source_profile_external_id: str | None,
        normalized_phone: str | None,
        image_hashes: list[int],
    ) -> tuple[int, MatchResult, int | None]:
        # A manual split is an explicit user override. Do not auto-merge it again on the
        # next profile refresh, even when the website reuses the same source-profile ID.
        if listing.match_decision == "MANUAL_SPLIT":
            return listing.person_id, MatchResult("MANUAL_SPLIT", "Manuelle Trennung: automatisches Re-Merge gesperrt."), None

        # Existing listing stays mapped unless stronger evidence resolves it elsewhere.
        candidates = db.scalars(select(Person).where(Person.person_id != listing.person_id)).all()
        evaluated: list[tuple[int, MatchResult]] = []
        for person in candidates:
            source_ids = set(db.scalars(select(SourceProfile.external_profile_id).where(
                SourceProfile.person_id == person.person_id, SourceProfile.external_profile_id.is_not(None)
            )).all())
            phones = set(db.scalars(select(PersonIdentifier.normalized_value).where(
                PersonIdentifier.person_id == person.person_id,
                PersonIdentifier.kind == "PHONE",
            )).all())
            old_hash_hex = set(db.scalars(select(PersonIdentifier.normalized_value).where(
                PersonIdentifier.person_id == person.person_id,
                PersonIdentifier.kind == "IMAGE_DHASH",
            )).all())
            old_hashes = []
            for value in old_hash_hex:
                try:
                    old_hashes.append(hash_from_hex(value))
                except ValueError:
                    pass
            result = match_identity(
                old_hashes, image_hashes,
                old_phones=phones, new_phone=normalized_phone,
                old_source_profile_ids=source_ids, new_source_profile_id=source_profile_external_id,
            )
            if result.decision != "NO_MATCH":
                evaluated.append((person.person_id, result))

        def rank(item: tuple[int, MatchResult]) -> tuple[int,int,int,int,int,int]:
            result = item[1]
            dr = {"MATCH":3,"STRONG_CANDIDATE":2,"CANDIDATE":1,"NO_MATCH":0}.get(result.decision,0)
            return (
                dr,
                int(result.source_profile_match), int(result.phone_match),
                result.strong_image_matches, result.very_strong_image_matches,
                -(result.best_distance if result.best_distance is not None else 999),
            )

        matches = sorted([x for x in evaluated if x[1].decision == "MATCH"], key=rank, reverse=True)
        if matches and (len(matches) == 1 or rank(matches[0]) > rank(matches[1])):
            return matches[0][0], matches[0][1], None
        weak = sorted([x for x in evaluated if x[1].decision in {"STRONG_CANDIDATE","CANDIDATE"}], key=rank, reverse=True)
        if weak:
            return listing.person_id, weak[0][1], weak[0][0]
        return listing.person_id, MatchResult("NO_MATCH", "Keine ausreichende Identity-Evidence."), None

    def _upsert_source_profile(self, db: Session, person_id: int, profile: ProfileData, *, allow_reassign: bool = True) -> SourceProfile | None:
        if not profile.source_profile_id and not profile.canonical_id:
            return None
        row = None
        if profile.source_profile_id:
            row = db.scalar(select(SourceProfile).where(
                SourceProfile.source == "LADIES", SourceProfile.external_profile_id == profile.source_profile_id
            ))
        if row is None and profile.canonical_id:
            row = db.scalar(select(SourceProfile).where(
                SourceProfile.source == "LADIES", SourceProfile.canonical_id == profile.canonical_id
            ))
        if row is None:
            row = SourceProfile(
                person_id=person_id, source="LADIES",
                external_profile_id=profile.source_profile_id,
                canonical_id=profile.canonical_id,
                link_reason="Websiteeigene Profil-ID/Canonical-ID",
            )
            db.add(row); db.flush()
        else:
            if row.person_id != person_id and not allow_reassign:
                return None
            row.person_id = person_id
            row.external_profile_id = profile.source_profile_id or row.external_profile_id
            row.canonical_id = profile.canonical_id or row.canonical_id
            row.last_seen_at = utcnow()
        return row

    def _touch_identifier(self, db: Session, person_id: int, listing_id: int, kind: str, normalized: str | None, raw: str | None) -> None:
        if not normalized:
            return
        row = db.scalar(select(PersonIdentifier).where(
            PersonIdentifier.person_id == person_id,
            PersonIdentifier.kind == kind,
            PersonIdentifier.normalized_value == normalized,
        ))
        if row is None:
            db.add(PersonIdentifier(
                person_id=person_id, listing_id=listing_id, kind=kind,
                normalized_value=normalized, raw_value=raw,
            ))
        else:
            row.last_seen_at = utcnow()
            row.listing_id = listing_id
            if raw:
                row.raw_value = raw

    @staticmethod
    def _fingerprint_payload(profile: ProfileData, normalized_phone: str | None, archived_media: list[tuple[Any, ArchivedMedia]]) -> dict[str, Any]:
        media = []
        for ref, archived in archived_media:
            media.append({
                "kind": ref.kind, "position": ref.position, "variant": ref.variant,
                "sha256": archived.asset.sha256 if archived.asset else None,
                "remote_base_url": archived.remote_base_url,
                "revision_token": ref.revision_token,
            })
        return {
            "name": profile.name,
            "city": profile.city,
            "postal_code": profile.postal_code,
            "hidden_location": profile.hidden_location,
            "category": profile.category,
            "description": profile.description,
            "published_at": profile.published_at,
            "source_profile_id": profile.source_profile_id,
            "canonical_id": profile.canonical_id,
            "phone": normalized_phone,
            "whatsapp": profile.whatsapp_url,
            "sms": profile.sms_url,
            "traits": profile.traits,
            "services": profile.services,
            "opening_times": profile.opening_times,
            "media": media,
        }

    def _add_snapshot_values(self, db: Session, snapshot: ListingSnapshot, profile: ProfileData) -> None:
        pos = 0
        for group, values in profile.traits.items():
            for value in values:
                pos += 1
                db.add(SnapshotValue(snapshot_id=snapshot.snapshot_id, kind="TRAIT", group_name=group, label=group, value=value, position=pos, provenance="DOM_VISIBLE", confidence_rank=100))
        pos = 0
        for group, values in profile.services.items():
            for value in values:
                pos += 1
                db.add(SnapshotValue(snapshot_id=snapshot.snapshot_id, kind="SERVICE", group_name=group, label=group, value=value, position=pos, provenance="DOM_VISIBLE", confidence_rank=100))
        pos = 0
        for day, value in profile.opening_times.items():
            pos += 1
            db.add(SnapshotValue(snapshot_id=snapshot.snapshot_id, kind="OPENING", group_name="Öffnungszeiten", label=day, value=value, position=pos, provenance="DOM_VISIBLE_OR_MICRODATA", confidence_rank=95))
        if profile.hidden_location:
            db.add(SnapshotValue(snapshot_id=snapshot.snapshot_id, kind="PROVENANCE", group_name="Hidden", label="data-ort", value=profile.hidden_location, position=1, provenance="DATA_ATTRIBUTE", confidence_rank=40))

    def _add_snapshot_media(self, db: Session, snapshot: ListingSnapshot, archived_media: list[tuple[Any, ArchivedMedia]]) -> None:
        for ref, archived in archived_media:
            db.add(SnapshotMedia(
                snapshot_id=snapshot.snapshot_id,
                media_asset_id=archived.asset.media_asset_id if archived.asset else None,
                media_source_id=archived.source.media_source_id,
                position=ref.position,
                kind=ref.kind,
                role=ref.role,
                remote_url=ref.url,
                remote_base_url=archived.remote_base_url,
                remote_variant=ref.variant,
                revision_token=ref.revision_token,
                etag=archived.etag,
                last_modified=archived.last_modified,
            ))

    def _create_change_events(self, db: Session, listing: Listing, old: ListingSnapshot, new: ListingSnapshot) -> None:
        scalar_fields = {
            "name": (old.name, new.name, "MEDIUM"),
            "city": (old.city, new.city, "HIGH"),
            "postal_code": (old.postal_code, new.postal_code, "MEDIUM"),
            "phone": (old.normalized_phone, new.normalized_phone, "HIGH"),
            "source_profile_id": (old.external_profile_id, new.external_profile_id, "HIGH"),
            "canonical_id": (old.canonical_id, new.canonical_id, "MEDIUM"),
            "description": (old.description, new.description, "LOW"),
        }
        for field, (before, after, importance) in scalar_fields.items():
            if before != after:
                db.add(ChangeEvent(
                    person_id=listing.person_id, listing_id=listing.listing_id,
                    from_snapshot_id=old.snapshot_id, to_snapshot_id=new.snapshot_id,
                    event_type="FIELD_CHANGED", field_key=field,
                    old_json=dumps(before), new_json=dumps(after), importance=importance,
                ))
        for field, before_json, after_json in (
            ("traits", old.traits_json, new.traits_json),
            ("services", old.services_json, new.services_json),
            ("opening_times", old.opening_times_json, new.opening_times_json),
        ):
            before, after = loads(before_json, {}), loads(after_json, {})
            if before != after:
                db.add(ChangeEvent(
                    person_id=listing.person_id, listing_id=listing.listing_id,
                    from_snapshot_id=old.snapshot_id, to_snapshot_id=new.snapshot_id,
                    event_type="STRUCTURED_CHANGED", field_key=field,
                    old_json=dumps(before), new_json=dumps(after), importance="HIGH" if field == "services" else "MEDIUM",
                ))
        old_media = self._snapshot_media_signature(db, old.snapshot_id)
        new_media = self._snapshot_media_signature(db, new.snapshot_id)
        added = [x for x in new_media if x not in old_media]
        removed = [x for x in old_media if x not in new_media]
        if added or removed:
            db.add(ChangeEvent(
                person_id=listing.person_id, listing_id=listing.listing_id,
                from_snapshot_id=old.snapshot_id, to_snapshot_id=new.snapshot_id,
                event_type="MEDIA_CHANGED", field_key="media", importance="HIGH",
                old_json=dumps({"removed": removed}), new_json=dumps({"added": added}),
            ))

    def _snapshot_media_signature(self, db: Session, snapshot_id: int) -> list[dict[str, Any]]:
        rows = db.execute(
            select(SnapshotMedia, MediaAsset)
            .outerjoin(MediaAsset, MediaAsset.media_asset_id == SnapshotMedia.media_asset_id)
            .where(SnapshotMedia.snapshot_id == snapshot_id)
            .order_by(SnapshotMedia.position, SnapshotMedia.kind)
        ).all()
        # Position and cache-busting revision tokens are intentionally excluded:
        # a reorder or a changed ``?h=`` value is not a real media addition/removal.
        # If the bytes behind a stable source actually change, sha256 changes and
        # the event remains visible.
        return [
            {"kind": sm.kind, "sha256": asset.sha256 if asset else None, "base": sm.remote_base_url}
            for sm, asset in rows
        ]

    def _record_identity_evidence(self, db: Session, listing: Listing, result: MatchResult, source_profile_id: str | None, phone: str | None) -> None:
        if source_profile_id:
            db.add(IdentityEvidence(listing_id=listing.listing_id, person_id=listing.person_id, candidate_person_id=listing.candidate_person_id, kind="SOURCE_PROFILE", value=source_profile_id, score=1.0 if result.source_profile_match else 0.5))
        if phone:
            db.add(IdentityEvidence(listing_id=listing.listing_id, person_id=listing.person_id, candidate_person_id=listing.candidate_person_id, kind="PHONE", value=phone, score=1.0 if result.phone_match else 0.5))
        if result.best_distance is not None:
            db.add(IdentityEvidence(listing_id=listing.listing_id, person_id=listing.person_id, candidate_person_id=listing.candidate_person_id, kind="IMAGE_DHASH", value=result.reason, best_distance=result.best_distance, score=max(0.0, 1.0 - result.best_distance / 16.0)))

    # ------------------------------------------------------------------
    # Status / user state
    # ------------------------------------------------------------------
    def update_listing_status(self, external_listing_id: str, *, status: str, http_status: int | None = None, details: dict[str, Any] | None = None) -> dict[str, Any]:
        status = status.upper()
        LOGGER.info("Listing status advert_id=%s status=%s http=%s", external_listing_id, status, http_status)
        if status not in {"ACTIVE", "UNAVAILABLE", "UNKNOWN"}:
            raise ValueError("Ungültiger Status")
        now = utcnow()
        with self.database.session() as db:
            listing = db.scalar(select(Listing).where(Listing.external_listing_id == str(external_listing_id)))
            if listing is None:
                raise KeyError(external_listing_id)
            previous = listing.current_status
            listing.current_status = status
            listing.status_checked_at = now
            if status == "ACTIVE":
                listing.last_seen_at = now
                listing.ended_at = None
            elif status == "UNAVAILABLE" and listing.ended_at is None:
                listing.ended_at = now
            db.add(ListingObservation(listing_id=listing.listing_id, observed_at=now, source="STATUS", status=status, http_status=http_status, details_json=dumps(details or {})))
            if previous != status:
                db.add(ChangeEvent(person_id=listing.person_id, listing_id=listing.listing_id, event_type="STATUS_CHANGED", field_key="status", old_json=dumps(previous), new_json=dumps(status), importance="HIGH"))
            return {"advert_id": external_listing_id, "status": status, "previous": previous, "person_id": listing.person_id}

    def set_user_state(self, person_id: int, data: dict[str, Any]) -> dict[str, Any]:
        LOGGER.info("User state update person_id=%s fields=%s", person_id, sorted(data))
        with self.database.session() as db:
            person = db.get(Person, int(person_id))
            if person is None:
                raise KeyError(person_id)
            state = db.get(PersonUserState, person.person_id)
            if state is None:
                state = PersonUserState(person_id=person.person_id); db.add(state)
            if "preference" in data:
                pref = str(data.get("preference") or "NONE").upper()
                if pref not in {"NONE","LIKE","DISLIKE"}:
                    raise ValueError("Ungültige preference")
                state.preference = pref
            if "favorite" in data:
                state.favorite = bool(data.get("favorite"))
            if "repeat_interest" in data:
                state.repeat_interest = str(data.get("repeat_interest") or "UNKNOWN")[:32]
            if "private_note" in data:
                state.private_note = str(data.get("private_note") or "")[:10000] or None
            state.updated_at = utcnow()
            db.flush()
            return self._state_payload(state)

    @staticmethod
    def _state_payload(state: PersonUserState | None) -> dict[str, Any]:
        return {
            "preference": state.preference if state else "NONE",
            "favorite": bool(state.favorite) if state else False,
            "repeat_interest": state.repeat_interest if state else "UNKNOWN",
            "private_note": state.private_note if state else "",
            "updated_at": iso(state.updated_at) if state else None,
        }

    # ------------------------------------------------------------------
    # Prices / encounters
    # ------------------------------------------------------------------
    def add_price_list(self, person_id: int, data: dict[str, Any]) -> dict[str, Any]:
        items = list(data.get("items") or [])
        if not items:
            raise ValueError("Mindestens ein Preiseintrag ist erforderlich")
        with self.database.session() as db:
            if db.get(Person, person_id) is None:
                raise KeyError(person_id)
            observed = parse_iso_datetime(data.get("observed_at")) or utcnow()
            listing_id = data.get("listing_id")
            row = PriceList(person_id=person_id, listing_id=int(listing_id) if listing_id else None, observed_at=observed, source=str(data.get("source") or "USER_ENTERED")[:32], currency="EUR", note=str(data.get("note") or "")[:4000] or None)
            db.add(row); db.flush()
            for item in items:
                amount = item.get("amount_cents")
                if amount is None and item.get("amount_eur") is not None:
                    amount = round(float(item.get("amount_eur")) * 100)
                amount = int(amount or 0)
                if amount < 0:
                    raise ValueError("Preis darf nicht negativ sein")
                db.add(PriceItem(
                    price_list_id=row.price_list_id,
                    item_type=str(item.get("item_type") or "BASE")[:24],
                    label=str(item.get("label") or "")[:256] or None,
                    duration_minutes=int(item["duration_minutes"]) if item.get("duration_minutes") not in {None,""} else None,
                    service=str(item.get("service") or "")[:256] or None,
                    amount_cents=amount,
                    note=str(item.get("note") or "")[:2000] or None,
                ))
            db.flush()
            return self._price_list_payload(db, row)

    def add_encounter(self, person_id: int, data: dict[str, Any]) -> dict[str, Any]:
        with self.database.session() as db:
            if db.get(Person, person_id) is None:
                raise KeyError(person_id)
            occurred = parse_iso_datetime(data.get("occurred_at")) or utcnow()
            def cents(name: str) -> int | None:
                value = data.get(name)
                if value in {None, ""}: return None
                if name.endswith("_eur"): return round(float(value) * 100)
                return int(value)
            row = Encounter(
                person_id=person_id,
                listing_id=int(data["listing_id"]) if data.get("listing_id") else None,
                occurred_at=occurred,
                duration_minutes=int(data["duration_minutes"]) if data.get("duration_minutes") not in {None,""} else None,
                base_price_cents=cents("base_price_eur"),
                total_price_cents=cents("total_price_eur"),
                overall_rating=int(data["overall_rating"]) if data.get("overall_rating") else None,
                sympathy_rating=int(data["sympathy_rating"]) if data.get("sympathy_rating") else None,
                photo_status=str(data.get("photo_status") or "UNKNOWN")[:24],
                service_accuracy=str(data.get("service_accuracy") or "UNKNOWN")[:24],
                hygiene=str(data.get("hygiene") or "UNKNOWN")[:24],
                kissing=str(data.get("kissing") or "UNKNOWN")[:24],
                negotiable=str(data.get("negotiable") or "UNKNOWN")[:24],
                note=str(data.get("note") or "")[:10000] or None,
            )
            db.add(row); db.flush()
            for item in list(data.get("services") or []):
                db.add(EncounterService(
                    encounter_id=row.encounter_id,
                    service_name=str(item.get("service_name") or item.get("name") or "")[:256],
                    amount_cents=round(float(item.get("amount_eur"))*100) if item.get("amount_eur") not in {None,""} else None,
                    performed=bool(item.get("performed", True)),
                    note=str(item.get("note") or "")[:2000] or None,
                ))
            db.flush()
            return self._encounter_payload(db, row)

    def update_encounter(self, person_id: int, encounter_id: int, data: dict[str, Any]) -> dict[str, Any]:
        """Edit one concrete encounter without collapsing multiple visits into one record."""
        def cents(key: str) -> int | None:
            if key not in data:
                return None
            value = data.get(key)
            return round(float(value) * 100) if value not in {None, ""} else None

        with self.database.session() as db:
            row = db.get(Encounter, int(encounter_id))
            if row is None or row.person_id != int(person_id):
                raise KeyError(encounter_id)
            if "listing_id" in data:
                row.listing_id = int(data["listing_id"]) if data.get("listing_id") else None
            if "occurred_at" in data:
                row.occurred_at = parse_iso_datetime(data.get("occurred_at")) or row.occurred_at
            if "duration_minutes" in data:
                row.duration_minutes = int(data["duration_minutes"]) if data.get("duration_minutes") not in {None, ""} else None
            if "base_price_eur" in data:
                row.base_price_cents = cents("base_price_eur")
            if "total_price_eur" in data:
                row.total_price_cents = cents("total_price_eur")
            if "overall_rating" in data:
                row.overall_rating = int(data["overall_rating"]) if data.get("overall_rating") else None
            if "sympathy_rating" in data:
                row.sympathy_rating = int(data["sympathy_rating"]) if data.get("sympathy_rating") else None
            for field in ("photo_status", "service_accuracy", "hygiene", "kissing", "negotiable"):
                if field in data:
                    setattr(row, field, str(data.get(field) or "UNKNOWN")[:24])
            if "note" in data:
                row.note = str(data.get("note") or "")[:10000] or None
            row.updated_at = utcnow()

            if "services" in data:
                db.execute(delete(EncounterService).where(EncounterService.encounter_id == row.encounter_id))
                for item in list(data.get("services") or []):
                    name = str(item.get("service_name") or item.get("name") or "")[:256]
                    if not name:
                        continue
                    db.add(EncounterService(
                        encounter_id=row.encounter_id,
                        service_name=name,
                        amount_cents=round(float(item.get("amount_eur")) * 100) if item.get("amount_eur") not in {None, ""} else None,
                        performed=bool(item.get("performed", True)),
                        note=str(item.get("note") or "")[:2000] or None,
                    ))
            db.flush()
            return self._encounter_payload(db, row)

    # ------------------------------------------------------------------
    # Manual identity repair
    # ------------------------------------------------------------------
    def merge_persons(self, source_person_id: int, target_person_id: int) -> dict[str, Any]:
        if source_person_id == target_person_id:
            return self.person_detail(target_person_id)
        with self.database.session() as db:
            self._merge_persons_in_session(db, source_person_id=source_person_id, target_person_id=target_person_id, action="MANUAL_MERGE")
        return self.person_detail(target_person_id)

    def _merge_persons_in_session(self, db: Session, *, source_person_id: int, target_person_id: int, action: str) -> None:
        source = db.get(Person, source_person_id); target = db.get(Person, target_person_id)
        if source is None or target is None or source_person_id == target_person_id:
            return
        src_state = db.get(PersonUserState, source_person_id); dst_state = db.get(PersonUserState, target_person_id)
        if dst_state is None:
            dst_state = PersonUserState(person_id=target_person_id); db.add(dst_state); db.flush()
        if src_state:
            if dst_state.preference == "NONE" and src_state.preference != "NONE": dst_state.preference = src_state.preference
            dst_state.favorite = dst_state.favorite or src_state.favorite
            if dst_state.repeat_interest == "UNKNOWN" and src_state.repeat_interest != "UNKNOWN": dst_state.repeat_interest = src_state.repeat_interest
            if src_state.private_note:
                dst_state.private_note = ((dst_state.private_note or "") + ("\n\n" if dst_state.private_note else "") + src_state.private_note)[:10000]

        listings = db.scalars(select(Listing).where(Listing.person_id == source_person_id)).all()
        for listing in listings:
            db.add(IdentityEvent(listing_id=listing.listing_id, previous_person_id=source_person_id, new_person_id=target_person_id, action=action, evidence_json=dumps({"reason": listing.match_reason})))
            listing.person_id = target_person_id
        # Candidate references should follow only when they pointed to the merged source.
        for listing in db.scalars(select(Listing).where(Listing.candidate_person_id == source_person_id)).all():
            listing.candidate_person_id = target_person_id
        for row in db.scalars(select(SourceProfile).where(SourceProfile.person_id == source_person_id)).all():
            duplicate = None
            if row.external_profile_id:
                duplicate = db.scalar(select(SourceProfile).where(SourceProfile.person_id == target_person_id, SourceProfile.source == row.source, SourceProfile.external_profile_id == row.external_profile_id))
            if duplicate:
                for l in db.scalars(select(Listing).where(Listing.source_profile_id == row.source_profile_id)).all(): l.source_profile_id = duplicate.source_profile_id
                db.delete(row)
            else:
                row.person_id = target_person_id
        for row in db.scalars(select(PersonIdentifier).where(PersonIdentifier.person_id == source_person_id)).all():
            duplicate = db.scalar(select(PersonIdentifier).where(PersonIdentifier.person_id == target_person_id, PersonIdentifier.kind == row.kind, PersonIdentifier.normalized_value == row.normalized_value))
            if duplicate:
                duplicate.last_seen_at = max(duplicate.last_seen_at, row.last_seen_at)
                db.delete(row)
            else:
                row.person_id = target_person_id
        for model in (PriceList, Encounter, ChangeEvent, IdentityEvidence):
            db.execute(update(model).where(model.person_id == source_person_id).values(person_id=target_person_id))
        if src_state: db.delete(src_state)
        target.first_seen_at = min(target.first_seen_at, source.first_seen_at)
        target.last_seen_at = max(target.last_seen_at, source.last_seen_at)
        target.updated_at = utcnow()
        if not target.display_name and source.display_name: target.display_name = source.display_name
        db.delete(source)
        db.flush()

    def split_listing(self, listing_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            listing = db.get(Listing, listing_id)
            if listing is None: raise KeyError(listing_id)
            old_person_id = listing.person_id
            snapshot = db.get(ListingSnapshot, listing.current_snapshot_id) if listing.current_snapshot_id else None
            new_person = Person(display_name=snapshot.name if snapshot else None, first_seen_at=listing.first_seen_at, last_seen_at=listing.last_seen_at)
            db.add(new_person); db.flush(); db.add(PersonUserState(person_id=new_person.person_id))
            listing.person_id = new_person.person_id
            listing.match_decision = "MANUAL_SPLIT"
            listing.candidate_person_id = None
            db.add(IdentityEvent(listing_id=listing.listing_id, previous_person_id=old_person_id, new_person_id=new_person.person_id, action="MANUAL_SPLIT"))
            # identifiers observed specifically on this listing follow the split
            for ident in db.scalars(select(PersonIdentifier).where(PersonIdentifier.listing_id == listing.listing_id)).all():
                ident.person_id = new_person.person_id
            if listing.source_profile_id:
                sp = db.get(SourceProfile, listing.source_profile_id)
                linked_count = db.scalar(select(func.count(Listing.listing_id)).where(Listing.source_profile_id == sp.source_profile_id)) if sp else 0
                if sp and linked_count == 1:
                    sp.person_id = new_person.person_id
                elif sp:
                    # Snapshot keeps the raw website ID, but the manually split listing
                    # must no longer claim the shared SourceProfile relation.
                    listing.source_profile_id = None
            db.flush()
            return {"listing_id": listing.listing_id, "old_person_id": old_person_id, "new_person_id": new_person.person_id}

    # ------------------------------------------------------------------
    # Visual analysis persistence
    # ------------------------------------------------------------------
    def save_visual_analysis(self, media_asset_id: int, data: dict[str, Any]) -> dict[str, Any]:
        engine = str(data.get("engine") or "mediapipe-browser")[:64]
        version = str(data.get("engine_version") or "1")[:64]
        with self.database.session() as db:
            if db.get(MediaAsset, media_asset_id) is None: raise KeyError(media_asset_id)
            row = db.scalar(select(VisualAnalysis).where(VisualAnalysis.media_asset_id == media_asset_id, VisualAnalysis.engine == engine, VisualAnalysis.engine_version == version))
            if row is None:
                row = VisualAnalysis(media_asset_id=media_asset_id, engine=engine, engine_version=version)
                db.add(row)
            row.score = float(data["score"]) if data.get("score") is not None else None
            row.label = str(data.get("label") or "")[:64] or None
            row.reasons_json = dumps(data.get("reasons") or [])
            row.diagnostics_json = dumps(data.get("diagnostics") or {})
            row.checked_at = utcnow()
            db.flush()
            return {"media_asset_id": media_asset_id, "engine": row.engine, "engine_version": row.engine_version, "score": row.score, "label": row.label, "reasons": loads(row.reasons_json, []), "checked_at": iso(row.checked_at)}

    # ------------------------------------------------------------------
    # Read models
    # ------------------------------------------------------------------
    @staticmethod
    def _feature_key(kind: str, label: str | None, value: str | None) -> tuple[str, str, str]:
        norm = lambda s: " ".join(str(s or "").casefold().replace("ß", "ss").split())
        return (str(kind or ""), norm(label), norm(value))

    def _person_analytics(self, db: Session, person: Person, listings: list[Listing], latest_snapshot: ListingSnapshot | None) -> dict[str, Any]:
        listing_ids = [x.listing_id for x in listings]
        snapshots = db.scalars(select(ListingSnapshot).where(ListingSnapshot.listing_id.in_(listing_ids))).all() if listing_ids else []
        snapshot_ids = [x.snapshot_id for x in snapshots]
        snapshot_listing = {x.snapshot_id: x.listing_id for x in snapshots}
        media_rows = db.execute(select(SnapshotMedia, MediaAsset).outerjoin(MediaAsset, MediaAsset.media_asset_id == SnapshotMedia.media_asset_id).where(SnapshotMedia.snapshot_id.in_(snapshot_ids))).all() if snapshot_ids else []
        assets: dict[int, tuple[SnapshotMedia, MediaAsset]] = {}
        asset_listings: dict[int, set[int]] = defaultdict(set)
        for sm, asset in media_rows:
            if asset and sm.media_asset_id:
                assets.setdefault(asset.media_asset_id, (sm, asset)); asset_listings[asset.media_asset_id].add(snapshot_listing.get(sm.snapshot_id, 0))
        image_ids = [aid for aid, (_, a) in assets.items() if a.kind == "IMAGE"]
        video_ids = [aid for aid, (_, a) in assets.items() if a.kind == "VIDEO"]
        analyses = db.scalars(select(VisualAnalysis).where(VisualAnalysis.media_asset_id.in_(image_ids)).order_by(desc(VisualAnalysis.checked_at))).all() if image_ids else []
        latest_analysis: dict[int, VisualAnalysis] = {}
        for row in analyses: latest_analysis.setdefault(row.media_asset_id, row)
        flagged = sum(1 for row in latest_analysis.values() if int(row.score or 0) >= 12)
        high = sum(1 for row in latest_analysis.values() if int(row.score or 0) >= 30)
        multi_face = 0
        for row in latest_analysis.values():
            diag = loads(row.diagnostics_json, {}) or {}
            if int(diag.get("faces") or 0) > 1: multi_face += 1
        shared = 0
        if image_ids:
            shared = len(set(db.scalars(select(SnapshotMedia.media_asset_id).join(ListingSnapshot, ListingSnapshot.snapshot_id == SnapshotMedia.snapshot_id).join(Listing, Listing.listing_id == ListingSnapshot.listing_id).where(SnapshotMedia.media_asset_id.in_(image_ids), Listing.person_id != person.person_id).distinct()).all()))
        cities = sorted({str(x.city).strip() for x in snapshots if x.city and str(x.city).strip()})
        names = sorted({str(x.name).strip() for x in snapshots if x.name and str(x.name).strip()})
        changes = db.scalars(select(ChangeEvent).where(ChangeEvent.person_id == person.person_id)).all()
        by_field: dict[str, int] = defaultdict(int)
        for x in changes: by_field[x.field_key or x.event_type or "other"] += 1

        # Transparent preference similarity: compare only explicit trait/service tokens of liked/disliked people.
        target_tokens: dict[tuple[str, str, str], str] = {}
        if latest_snapshot:
            for v in db.scalars(select(SnapshotValue).where(SnapshotValue.snapshot_id == latest_snapshot.snapshot_id, SnapshotValue.kind.in_(["TRAIT", "SERVICE"]))).all():
                key = self._feature_key(v.kind, v.label or v.group_name, v.value); target_tokens[key] = f"{v.label or v.group_name}: {v.value}" if v.kind == "TRAIT" else str(v.value)
        pref_rows = db.execute(select(PersonUserState.person_id, PersonUserState.preference, SnapshotValue.kind, SnapshotValue.label, SnapshotValue.group_name, SnapshotValue.value).join(Listing, Listing.person_id == PersonUserState.person_id).join(SnapshotValue, SnapshotValue.snapshot_id == Listing.current_snapshot_id).where(PersonUserState.preference.in_(["LIKE", "DISLIKE"]), PersonUserState.person_id != person.person_id, Listing.current_snapshot_id.is_not(None), SnapshotValue.kind.in_(["TRAIT", "SERVICE"]))).all()
        per_person: dict[int, tuple[str, set[tuple[str, str, str]]]] = {}
        for pid, pref, kind, label, group, value in pref_rows:
            bucket = per_person.setdefault(int(pid), (pref, set()))[1]; bucket.add(self._feature_key(kind, label or group, value))
        like_samples = sum(1 for pref, _ in per_person.values() if pref == "LIKE")
        dislike_samples = sum(1 for pref, _ in per_person.values() if pref == "DISLIKE")
        evidence = []
        for key, label in target_tokens.items():
            lp = sum(1 for pref, tokens in per_person.values() if pref == "LIKE" and key in tokens)
            dn = sum(1 for pref, tokens in per_person.values() if pref == "DISLIKE" and key in tokens)
            if lp + dn:
                signal = (lp - dn) / (lp + dn)
                evidence.append((signal, lp + dn, label, lp, dn))
        if like_samples + dislike_samples >= 3 and evidence:
            weighted = sum(sig * min(3, n) for sig, n, *_ in evidence) / max(1, sum(min(3, n) for _, n, *_ in evidence))
            affinity_score = max(0, min(100, round(50 + weighted * 38)))
            positive = [{"label": label, "likes": lp, "dislikes": dn} for sig, n, label, lp, dn in sorted(evidence, key=lambda x: (x[0], x[1]), reverse=True) if sig > 0][:5]
            negative = [{"label": label, "likes": lp, "dislikes": dn} for sig, n, label, lp, dn in sorted(evidence, key=lambda x: (x[0], -x[1])) if sig < 0][:4]
            affinity = {"score": affinity_score, "like_samples": like_samples, "dislike_samples": dislike_samples, "positive": positive, "negative": negative}
        else:
            affinity = {"score": None, "like_samples": like_samples, "dislike_samples": dislike_samples, "positive": [], "negative": [], "note": "Mindestens drei bewertete Personen mit vergleichbaren Merkmalen nötig"}
        return {
            "media": {"unique_media": len(assets), "image_count": len(image_ids), "video_count": len(video_ids), "analyzed_images": len(latest_analysis), "flagged_images": flagged, "high_flag_images": high, "multi_face_images": multi_face, "reused_across_listings": sum(1 for ids in asset_listings.values() if len({x for x in ids if x}) > 1), "shared_with_other_people": shared},
            "consistency": {"city_count": len(cities), "cities": cities, "name_count": len(names), "names": names, "changes_by_field": dict(by_field)},
            "affinity": affinity,
        }

    def person_detail(self, person_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            person = db.get(Person, int(person_id))
            if person is None: raise KeyError(person_id)
            state = db.get(PersonUserState, person.person_id)
            listings = db.scalars(select(Listing).where(Listing.person_id == person.person_id).order_by(desc(Listing.last_seen_at))).all()
            source_profiles = db.scalars(select(SourceProfile).where(SourceProfile.person_id == person.person_id).order_by(SourceProfile.source_profile_id)).all()
            identifiers = db.scalars(select(PersonIdentifier).where(PersonIdentifier.person_id == person.person_id).order_by(PersonIdentifier.kind, PersonIdentifier.first_seen_at)).all()
            changes = db.scalars(select(ChangeEvent).where(ChangeEvent.person_id == person.person_id).order_by(desc(ChangeEvent.created_at)).limit(100)).all()
            prices = db.scalars(select(PriceList).where(PriceList.person_id == person.person_id).order_by(desc(PriceList.observed_at))).all()
            encounters = db.scalars(select(Encounter).where(Encounter.person_id == person.person_id).order_by(desc(Encounter.occurred_at))).all()
            latest_snapshot = None
            for listing in listings:
                if listing.current_snapshot_id:
                    snap = db.get(ListingSnapshot, listing.current_snapshot_id)
                    if snap and (latest_snapshot is None or snap.captured_at > latest_snapshot.captured_at): latest_snapshot = snap
            return {
                "person": {
                    "person_id": person.person_id,
                    "display_name": person.display_name,
                    "first_seen_at": iso(person.first_seen_at),
                    "last_seen_at": iso(person.last_seen_at),
                    "listing_count": len(listings),
                    "reappear_count": max(0, len(listings)-1),
                },
                "user_state": self._state_payload(state),
                "source_profiles": [{"source_profile_id": x.source_profile_id, "source": x.source, "external_profile_id": x.external_profile_id, "canonical_id": x.canonical_id, "first_seen_at": iso(x.first_seen_at), "last_seen_at": iso(x.last_seen_at)} for x in source_profiles],
                "identifiers": [{"kind": x.kind, "value": x.normalized_value, "first_seen_at": iso(x.first_seen_at), "last_seen_at": iso(x.last_seen_at)} for x in identifiers if x.kind != "IMAGE_DHASH"],
                "listings": [self._listing_payload(db, x) for x in listings],
                "latest_snapshot": self._snapshot_payload(db, latest_snapshot) if latest_snapshot else None,
                "timeline": [self._change_payload(x) for x in changes],
                "price_lists": [self._price_list_payload(db, x) for x in prices],
                "encounters": [self._encounter_payload(db, x) for x in encounters],
                "analytics": self._person_analytics(db, person, listings, latest_snapshot),
            }

    def snapshot_detail(self, snapshot_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            snapshot = db.get(ListingSnapshot, int(snapshot_id))
            if snapshot is None:
                raise KeyError(snapshot_id)
            payload = self._snapshot_payload(db, snapshot)
            listing = db.get(Listing, snapshot.listing_id)
            return {"snapshot": payload, "listing": self._listing_payload(db, listing) if listing else None}

    def listing_detail(self, external_listing_id: str) -> dict[str, Any]:
        with self.database.session() as db:
            listing = db.scalar(select(Listing).where(Listing.external_listing_id == str(external_listing_id)))
            if listing is None: raise KeyError(external_listing_id)
            snapshot = db.get(ListingSnapshot, listing.current_snapshot_id) if listing.current_snapshot_id else None
            observations = db.scalars(select(ListingObservation).where(ListingObservation.listing_id == listing.listing_id).order_by(desc(ListingObservation.observed_at)).limit(100)).all()
            snapshots = db.scalars(select(ListingSnapshot).where(ListingSnapshot.listing_id == listing.listing_id).order_by(desc(ListingSnapshot.captured_at))).all()
            return {
                "listing": self._listing_payload(db, listing),
                "profile": self._snapshot_payload(db, snapshot) if snapshot else None,
                "snapshots": [{"snapshot_id": x.snapshot_id, "captured_at": iso(x.captured_at), "fingerprint": x.fingerprint} for x in snapshots],
                "observations": [{"source": x.source, "status": x.status, "observed_at": iso(x.observed_at), "http_status": x.http_status, "snapshot_id": x.snapshot_id} for x in observations],
                "person": self.person_detail(listing.person_id),
            }

    def lists(self, tab: str = "LIKE") -> list[dict[str, Any]]:
        tab = tab.upper()
        with self.database.session() as db:
            q = select(Person, PersonUserState).join(PersonUserState, PersonUserState.person_id == Person.person_id)
            if tab == "FAVORITE":
                q = q.where(PersonUserState.favorite.is_(True))
            elif tab in {"LIKE", "DISLIKE"}:
                q = q.where(PersonUserState.preference == tab)
            else:
                q = q.where(or_(PersonUserState.preference != "NONE", PersonUserState.favorite.is_(True)))
            rows = db.execute(q.order_by(desc(PersonUserState.updated_at))).all()
            out = []
            for person, state in rows:
                listing = db.scalar(
                    select(Listing).where(Listing.person_id == person.person_id)
                    .order_by(desc(Listing.last_seen_at)).limit(1)
                )
                snap = db.get(ListingSnapshot, listing.current_snapshot_id) if listing and listing.current_snapshot_id else None
                latest_sr = db.scalar(
                    select(SearchResult).where(SearchResult.listing_id == listing.listing_id)
                    .order_by(desc(SearchResult.observed_at)).limit(1)
                ) if listing else None
                person_sr = db.scalar(
                    select(SearchResult).join(Listing, SearchResult.listing_id == Listing.listing_id)
                    .where(Listing.person_id == person.person_id, SearchResult.city.is_not(None))
                    .order_by(desc(SearchResult.observed_at)).limit(1)
                )
                fallback_snap = db.scalar(
                    select(ListingSnapshot).join(Listing, ListingSnapshot.listing_id == Listing.listing_id)
                    .where(Listing.person_id == person.person_id, ListingSnapshot.city.is_not(None))
                    .order_by(desc(ListingSnapshot.captured_at)).limit(1)
                )
                thumb = None
                if snap:
                    media = db.scalar(
                        select(SnapshotMedia)
                        .where(SnapshotMedia.snapshot_id == snap.snapshot_id, SnapshotMedia.kind == "IMAGE")
                        .order_by(SnapshotMedia.position).limit(1)
                    )
                    if media and media.media_asset_id:
                        asset = db.get(MediaAsset, media.media_asset_id)
                        thumb = f"/archive/{asset.local_path}" if asset else media.remote_url
                    else:
                        thumb = media.remote_url if media else None
                if thumb is None and latest_sr:
                    thumb = latest_sr.thumbnail_url
                if thumb is None and person_sr:
                    thumb = person_sr.thumbnail_url
                count = db.scalar(select(func.count(Listing.listing_id)).where(Listing.person_id == person.person_id)) or 0
                recent_change = db.scalar(
                    select(ChangeEvent).where(ChangeEvent.person_id == person.person_id)
                    .order_by(desc(ChangeEvent.created_at)).limit(1)
                )
                out.append({
                    "person_id": person.person_id,
                    "name": (snap.name if snap and snap.name else None) or (latest_sr.name if latest_sr and latest_sr.name else None) or (person_sr.name if person_sr and person_sr.name else None) or person.display_name,
                    "city": (snap.city if snap and snap.city else None) or (fallback_snap.city if fallback_snap and fallback_snap.city else None) or (latest_sr.city if latest_sr and latest_sr.city else None) or (person_sr.city if person_sr and person_sr.city else None),
                    "thumbnail_url": thumb,
                    "preference": state.preference,
                    "favorite": state.favorite,
                    "listing_count": int(count),
                    "reappear_count": max(0, int(count) - 1),
                    "last_seen_at": iso(person.last_seen_at),
                    "latest_advert_id": listing.external_listing_id if listing else None,
                    "status": listing.current_status if listing else "UNKNOWN",
                    "status_checked_at": iso(listing.status_checked_at) if listing else None,
                    "recent_change": self._change_payload(recent_change) if recent_change else None,
                })
            return out

    def dashboard(self) -> dict[str, Any]:
        with self.database.session() as db:
            persons = db.scalar(select(func.count(Person.person_id))) or 0
            listings = db.scalar(select(func.count(Listing.listing_id))) or 0
            active = db.scalar(select(func.count(Listing.listing_id)).where(Listing.current_status == "ACTIVE")) or 0
            snapshots = db.scalar(select(func.count(ListingSnapshot.snapshot_id))) or 0
            media = db.scalar(select(func.count(MediaAsset.media_asset_id))) or 0
            changes = db.scalar(select(func.count(ChangeEvent.change_event_id))) or 0
            encounters = db.scalar(select(func.count(Encounter.encounter_id))) or 0
            visual = db.scalar(select(func.count(VisualAnalysis.visual_analysis_id))) or 0
            likes = db.scalar(select(func.count(PersonUserState.person_id)).where(PersonUserState.preference == "LIKE")) or 0
            dislikes = db.scalar(select(func.count(PersonUserState.person_id)).where(PersonUserState.preference == "DISLIKE")) or 0
            favorites = db.scalar(select(func.count(PersonUserState.person_id)).where(PersonUserState.favorite.is_(True))) or 0
            returns = db.scalar(
                select(func.count()).select_from(
                    select(Listing.person_id).group_by(Listing.person_id)
                    .having(func.count(Listing.listing_id) > 1).subquery()
                )
            ) or 0
            recent = db.scalars(select(ChangeEvent).order_by(desc(ChangeEvent.created_at)).limit(30)).all()
            search_total = db.scalar(select(func.count(SearchRun.search_run_id)).where(SearchRun.finished_at.is_not(None))) or 0
            search_rows = db.scalars(select(SearchRun).where(SearchRun.finished_at.is_not(None)).order_by(desc(SearchRun.started_at)).limit(250)).all()
            filter_counts: dict[str, int] = defaultdict(int)
            for run in search_rows:
                filters = loads(run.filters_json, {}) or {}
                selected = filters.get("selected") if isinstance(filters, dict) else None
                if isinstance(selected, dict):
                    for values in selected.values():
                        if isinstance(values, list):
                            for value in values:
                                if value: filter_counts[str(value)] += 1
            avg_results = (sum(int(x.advert_count or 0) for x in search_rows) / len(search_rows)) if search_rows else None
            decisions = int(likes) + int(dislikes)
            usage = {
                "searches": int(search_total),
                "decisions": decisions,
                "like_rate": round(int(likes) / decisions * 100) if decisions else None,
                "favorite_rate": round(int(favorites) / int(likes) * 100) if likes else None,
                "avg_results": round(avg_results, 1) if avg_results is not None else None,
                "top_filters": [{"label": k, "count": v} for k, v in sorted(filter_counts.items(), key=lambda kv: (-kv[1], kv[0].casefold()))[:8]],
            }
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
                "recent_changes": [self._change_payload(x) for x in recent],
                "usage": usage,
            }

    def history_relistings(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(250, int(limit)))
        with self.database.session() as db:
            person_ids = db.scalars(
                select(Listing.person_id).group_by(Listing.person_id)
                .having(func.count(Listing.listing_id) > 1)
                .order_by(desc(func.max(Listing.last_seen_at))).limit(limit)
            ).all()
            out = []
            for person_id in person_ids:
                person = db.get(Person, person_id)
                listings = db.scalars(select(Listing).where(Listing.person_id == person_id).order_by(desc(Listing.last_seen_at))).all()
                latest = listings[0] if listings else None
                snap = db.get(ListingSnapshot, latest.current_snapshot_id) if latest and latest.current_snapshot_id else None
                sr = db.scalar(select(SearchResult).where(SearchResult.listing_id == latest.listing_id).order_by(desc(SearchResult.observed_at)).limit(1)) if latest else None
                person_sr = db.scalar(select(SearchResult).join(Listing, SearchResult.listing_id == Listing.listing_id).where(Listing.person_id == person_id).order_by(desc(SearchResult.observed_at)).limit(1))
                fallback_snap = db.scalar(select(ListingSnapshot).join(Listing, ListingSnapshot.listing_id == Listing.listing_id).where(Listing.person_id == person_id, ListingSnapshot.city.is_not(None)).order_by(desc(ListingSnapshot.captured_at)).limit(1))
                thumb = None
                if snap:
                    sm = db.scalar(select(SnapshotMedia).where(SnapshotMedia.snapshot_id == snap.snapshot_id, SnapshotMedia.kind == "IMAGE").order_by(SnapshotMedia.position).limit(1))
                    if sm:
                        if sm.media_asset_id:
                            asset = db.get(MediaAsset, sm.media_asset_id)
                            thumb = f"/archive/{asset.local_path}" if asset else sm.remote_url
                        else:
                            thumb = sm.remote_url
                if not thumb and sr:
                    thumb = sr.thumbnail_url
                out.append({
                    "person_id": person_id,
                    "name": (snap.name if snap and snap.name else None) or (sr.name if sr and sr.name else None) or (person_sr.name if person_sr and person_sr.name else None) or (person.display_name if person else None),
                    "city": (snap.city if snap and snap.city else None) or (fallback_snap.city if fallback_snap and fallback_snap.city else None) or (sr.city if sr and sr.city else None) or (person_sr.city if person_sr and person_sr.city else None),
                    "thumbnail_url": thumb or (person_sr.thumbnail_url if person_sr else None),
                    "listing_count": len(listings),
                    "last_seen_at": iso(person.last_seen_at) if person else (iso(latest.last_seen_at) if latest else None),
                })
            return out

    def history_changes(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(250, int(limit)))
        with self.database.session() as db:
            rows = db.scalars(select(ChangeEvent).order_by(desc(ChangeEvent.created_at)).limit(limit)).all()
            return [self._change_payload(x) for x in rows]

    def listing_target(self, external_listing_id: str) -> dict[str, Any] | None:
        with self.database.session() as db:
            listing = db.scalar(select(Listing).where(Listing.external_listing_id == str(external_listing_id)))
            if listing is None:
                return None
            return {
                "listing_id": listing.listing_id,
                "advert_id": listing.external_listing_id,
                "person_id": listing.person_id,
                "current_url": listing.current_url,
                "category": listing.category,
                "current_status": listing.current_status,
            }

    def status_targets(self) -> list[dict[str, Any]]:
        with self.database.session() as db:
            rows = db.scalars(select(Listing).join(PersonUserState, PersonUserState.person_id==Listing.person_id).where(or_(PersonUserState.preference != "NONE", PersonUserState.favorite.is_(True))).order_by(desc(Listing.last_seen_at))).all()
            return [{"advert_id":x.external_listing_id,"profile_url":x.current_url,"person_id":x.person_id,"status":x.current_status} for x in rows]

    def _listing_payload(self, db: Session, listing: Listing) -> dict[str, Any]:
        snapshot = db.get(ListingSnapshot, listing.current_snapshot_id) if listing.current_snapshot_id else None
        return {
            "listing_id": listing.listing_id,
            "advert_id": listing.external_listing_id,
            "person_id": listing.person_id,
            "source_profile_id": listing.source_profile_id,
            "current_url": listing.current_url,
            "category": listing.category,
            "published_at": iso(listing.published_at),
            "first_seen_at": iso(listing.first_seen_at),
            "last_seen_at": iso(listing.last_seen_at),
            "current_status": listing.current_status,
            "status_checked_at": iso(listing.status_checked_at),
            "ended_at": iso(listing.ended_at),
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

    def _profile_payload(self, db: Session, listing: Listing, snapshot: ListingSnapshot) -> dict[str, Any]:
        person = db.get(Person, listing.person_id)
        state = db.get(PersonUserState, listing.person_id)
        count = db.scalar(select(func.count(Listing.listing_id)).where(Listing.person_id == listing.person_id)) or 0
        return {
            "listing": self._listing_payload(db, listing),
            "profile": self._snapshot_payload(db, snapshot),
            "identity": {
                "person_id": listing.person_id,
                "display_name": person.display_name if person else snapshot.name,
                "preference": state.preference if state else "NONE",
                "favorite": state.favorite if state else False,
                "known_listing_count": int(count),
                "reappear_count": max(0, int(count)-1),
                "match_decision": listing.match_decision,
                "match_reason": listing.match_reason,
                "candidate_person_id": listing.candidate_person_id,
                "best_distance": listing.match_best_distance,
                "image_matches": listing.match_image_count,
                "strong_image_matches": listing.match_strong_image_count,
                "phone_match": listing.match_phone,
                "source_profile_match": listing.match_source_profile,
                "person_first_seen_at": iso(person.first_seen_at) if person else None,
                "person_last_seen_at": iso(person.last_seen_at) if person else None,
            },
        }

    def _snapshot_payload(self, db: Session, snapshot: ListingSnapshot | None) -> dict[str, Any] | None:
        if snapshot is None: return None
        media_rows = db.execute(select(SnapshotMedia, MediaAsset).outerjoin(MediaAsset, MediaAsset.media_asset_id==SnapshotMedia.media_asset_id).where(SnapshotMedia.snapshot_id==snapshot.snapshot_id).order_by(SnapshotMedia.position, SnapshotMedia.kind)).all()
        media=[]
        for sm, asset in media_rows:
            visual = None
            if asset:
                va = db.scalar(select(VisualAnalysis).where(VisualAnalysis.media_asset_id == asset.media_asset_id).order_by(desc(VisualAnalysis.checked_at)).limit(1))
                if va:
                    visual = {"engine":va.engine,"engine_version":va.engine_version,"score":va.score,"label":va.label,"reasons":loads(va.reasons_json,[]),"diagnostics":loads(va.diagnostics_json,{}),"checked_at":iso(va.checked_at)}
            media.append({
                "snapshot_media_id":sm.snapshot_media_id,"media_asset_id":sm.media_asset_id,"kind":sm.kind,"position":sm.position,"role":sm.role,
                "url":sm.remote_url,"remote_base_url":sm.remote_base_url,"variant":sm.remote_variant,"revision_token":sm.revision_token,"etag":sm.etag,"last_modified":sm.last_modified,
                "sha256":asset.sha256 if asset else None,"dhash":asset.dhash if asset else None,"width":asset.width if asset else None,"height":asset.height if asset else None,"byte_size":asset.byte_size if asset else None,
                "local_url":f"/archive/{asset.local_path}" if asset else None,"visual_analysis":visual,
            })
        return {
            "snapshot_id":snapshot.snapshot_id,"captured_at":iso(snapshot.captured_at),"fingerprint":snapshot.fingerprint,
            "advert_id":snapshot.listing.external_listing_id if snapshot.listing else None,
            "source_url":snapshot.source_url,"name":snapshot.name,"city":snapshot.city,"postal_code":snapshot.postal_code,"hidden_location":snapshot.hidden_location,"category":snapshot.category,"description":snapshot.description,"published_at":iso(snapshot.published_at),
            "source_profile_id":snapshot.external_profile_id,"canonical_id":snapshot.canonical_id,"phone_display":snapshot.phone_display,"normalized_phone":snapshot.normalized_phone,"whatsapp_url":snapshot.whatsapp_url,"sms_url":snapshot.sms_url,"tel_url":snapshot.tel_url,
            "traits":loads(snapshot.traits_json,{}),"services":loads(snapshot.services_json,{}),"opening_times":loads(snapshot.opening_times_json,{}),"auxiliary":loads(snapshot.auxiliary_json,{}),"media":media,
            "raw_html_sha256":snapshot.raw_html_sha256,
        }

    def _change_payload(self, x: ChangeEvent) -> dict[str, Any]:
        return {"change_event_id":x.change_event_id,"person_id":x.person_id,"listing_id":x.listing_id,"from_snapshot_id":x.from_snapshot_id,"to_snapshot_id":x.to_snapshot_id,"event_type":x.event_type,"field_key":x.field_key,"old":loads(x.old_json),"new":loads(x.new_json),"importance":x.importance,"created_at":iso(x.created_at)}

    def _price_list_payload(self, db: Session, row: PriceList) -> dict[str, Any]:
        items = db.scalars(select(PriceItem).where(PriceItem.price_list_id==row.price_list_id).order_by(PriceItem.price_item_id)).all()
        return {"price_list_id":row.price_list_id,"person_id":row.person_id,"listing_id":row.listing_id,"observed_at":iso(row.observed_at),"source":row.source,"currency":row.currency,"note":row.note,"items":[{"price_item_id":i.price_item_id,"item_type":i.item_type,"label":i.label,"duration_minutes":i.duration_minutes,"service":i.service,"amount_cents":i.amount_cents,"amount_eur":i.amount_cents/100,"note":i.note} for i in items]}

    def _encounter_payload(self, db: Session, row: Encounter) -> dict[str, Any]:
        items = db.scalars(select(EncounterService).where(EncounterService.encounter_id==row.encounter_id).order_by(EncounterService.encounter_service_id)).all()
        return {"encounter_id":row.encounter_id,"person_id":row.person_id,"listing_id":row.listing_id,"occurred_at":iso(row.occurred_at),"duration_minutes":row.duration_minutes,"base_price_eur":row.base_price_cents/100 if row.base_price_cents is not None else None,"total_price_eur":row.total_price_cents/100 if row.total_price_cents is not None else None,"overall_rating":row.overall_rating,"sympathy_rating":row.sympathy_rating,"photo_status":row.photo_status,"service_accuracy":row.service_accuracy,"hygiene":row.hygiene,"kissing":row.kissing,"negotiable":row.negotiable,"note":row.note,"services":[{"service_name":i.service_name,"amount_eur":i.amount_cents/100 if i.amount_cents is not None else None,"performed":i.performed,"note":i.note} for i in items]}
