from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import orjson
from sqlalchemy import delete, desc, func, or_, select, update
from sqlalchemy.orm import Session

from ladies_v9.database import Database
from ladies_v9.domain import ProfileData
from ladies_v9.identity import MatchResult, hash_from_hex, match_identity, normalize_phone_number
from ladies_v9.media import ArchiveManager, ArchivedMedia
from ladies_v9.models import (
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
    SnapshotMedia,
    SnapshotValue,
    SourceProfile,
    utcnow,
)
from ladies_v9.utils import parse_iso_datetime


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


class PersonRepository:
    """Person, listing, identity and user-entered person data boundary.

    Profile ingestion, identity evidence, merge/split and state-changing person
    operations are repository-owned. During the final read-model migration only
    the large detail/list serializers still delegate to the legacy store.
    """

    def __init__(
        self,
        database: Database,
        archive: ArchiveManager,
        settings_repository,
        read_store,
    ):
        self.database = database
        self.archive = archive
        self.settings_repository = settings_repository
        self._read_store = read_store

    def listing_target(self, advert_id):
        with self.database.session() as db:
            listing = db.scalar(
                select(Listing).where(Listing.external_listing_id == str(advert_id))
            )
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

    def ingest_profile(
        self,
        *,
        external_listing_id: str,
        profile: ProfileData,
        raw_html: str,
        http_session,
    ) -> dict[str, Any]:
        settings = self.settings_repository.get_all()
        now = utcnow()
        with self.database.session() as db:
            listing = db.scalar(select(Listing).where(
                Listing.source == "LADIES",
                Listing.external_listing_id == str(external_listing_id),
            ))
            if listing is None:
                person = Person(display_name=profile.name or None)
                db.add(person)
                db.flush()
                db.add(PersonUserState(person_id=person.person_id))
                listing = Listing(
                    person_id=person.person_id,
                    source="LADIES",
                    external_listing_id=str(external_listing_id),
                    current_url=profile.source_url,
                    category=profile.category,
                    current_status="ACTIVE",
                    match_decision="UNRESOLVED",
                )
                db.add(listing)
                db.flush()

            archived_media: list[tuple[Any, ArchivedMedia]] = []
            image_hashes: list[int] = []
            for ref in sorted(profile.media, key=lambda item: (item.position, item.kind)):
                should_download = ref.kind == "IMAGE" or bool(settings.get("archive_videos"))
                try:
                    archived = self.archive.ensure_remote_media(
                        db, http_session, ref, download=should_download
                    )
                except Exception:
                    archived = self.archive.ensure_remote_media(
                        db, http_session, ref, download=False
                    )
                archived_media.append((ref, archived))
                if archived.asset and archived.asset.dhash and ref.kind == "IMAGE":
                    try:
                        value = hash_from_hex(archived.asset.dhash)
                        if value not in image_hashes:
                            image_hashes.append(value)
                    except ValueError:
                        pass

            normalized_phone = normalize_phone_number(
                profile.phone_display or profile.tel_url or profile.whatsapp_url
            )
            target_person_id, result, candidate_person_id = self._resolve_identity(
                db,
                listing=listing,
                source_profile_external_id=profile.source_profile_id,
                normalized_phone=normalized_phone,
                image_hashes=image_hashes,
            )
            if target_person_id != listing.person_id and result.decision == "MATCH":
                old_person_id = listing.person_id
                self._merge_persons_in_session(
                    db,
                    source_person_id=old_person_id,
                    target_person_id=target_person_id,
                    action="AUTO_MATCH",
                )
                listing = db.get(Listing, listing.listing_id)

            listing.match_decision = (
                result.decision
                if result.decision != "NO_MATCH"
                else (listing.match_decision if listing.match_decision != "UNRESOLVED" else "NO_MATCH")
            )
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

            source_profile = self._upsert_source_profile(
                db,
                listing.person_id,
                profile,
                allow_reassign=listing.match_decision != "MANUAL_SPLIT",
            )
            if source_profile:
                listing.source_profile_id = source_profile.source_profile_id

            self._touch_identifier(
                db,
                listing.person_id,
                listing.listing_id,
                "PHONE",
                normalized_phone,
                profile.phone_display,
            )
            self._touch_identifier(
                db,
                listing.person_id,
                listing.listing_id,
                "SOURCE_PROFILE",
                profile.source_profile_id,
                profile.source_profile_id,
            )
            for ref, archived in archived_media:
                if archived.asset and archived.asset.dhash:
                    self._touch_identifier(
                        db,
                        listing.person_id,
                        listing.listing_id,
                        "IMAGE_DHASH",
                        archived.asset.dhash,
                        ref.url,
                    )

            fingerprint_payload = self._fingerprint_payload(
                profile, normalized_phone, archived_media
            )
            fingerprint = hashlib.sha256(
                orjson.dumps(fingerprint_payload, option=orjson.OPT_SORT_KEYS)
            ).hexdigest()
            previous = (
                db.get(ListingSnapshot, listing.current_snapshot_id)
                if listing.current_snapshot_id else None
            )
            existing = db.scalar(select(ListingSnapshot).where(
                ListingSnapshot.listing_id == listing.listing_id,
                ListingSnapshot.fingerprint == fingerprint,
            ))
            if existing is not None:
                listing.current_snapshot_id = existing.snapshot_id
                db.add(ListingObservation(
                    listing_id=listing.listing_id,
                    observed_at=now,
                    source="PROFILE",
                    status="ACTIVE",
                    http_status=200,
                    snapshot_id=existing.snapshot_id,
                    details_json=_dumps({"snapshot_changed": False}),
                ))
                return self._read_store._profile_payload(db, listing, existing)

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
                traits_json=_dumps(profile.traits),
                services_json=_dumps(profile.services),
                opening_times_json=_dumps(profile.opening_times),
                auxiliary_json=_dumps({
                    "provenance": profile.provenance,
                    "auxiliary": profile.auxiliary,
                }),
                raw_html_sha256=raw_sha,
                raw_html_path=raw_path,
            )
            db.add(snapshot)
            db.flush()
            self._add_snapshot_values(db, snapshot, profile)
            self._add_snapshot_media(db, snapshot, archived_media)
            listing.current_snapshot_id = snapshot.snapshot_id

            if previous is None:
                db.add(ChangeEvent(
                    person_id=listing.person_id,
                    listing_id=listing.listing_id,
                    to_snapshot_id=snapshot.snapshot_id,
                    event_type="LISTING_FIRST_SNAPSHOT",
                    importance="MEDIUM",
                    new_json=_dumps({
                        "advert_id": listing.external_listing_id,
                        "name": profile.name,
                    }),
                ))
            else:
                self._create_change_events(db, listing, previous, snapshot)

            if result.decision == "MATCH" and result.reason != "Externe Anzeigen-ID ist bereits bekannt.":
                db.add(ChangeEvent(
                    person_id=listing.person_id,
                    listing_id=listing.listing_id,
                    to_snapshot_id=snapshot.snapshot_id,
                    event_type="NEW_LISTING_FOR_PERSON",
                    importance="HIGH",
                    new_json=_dumps({
                        "advert_id": listing.external_listing_id,
                        "reason": result.reason,
                    }),
                ))

            self._record_identity_evidence(
                db,
                listing,
                result,
                profile.source_profile_id,
                normalized_phone,
            )
            db.add(ListingObservation(
                listing_id=listing.listing_id,
                observed_at=now,
                source="PROFILE",
                status="ACTIVE",
                http_status=200,
                snapshot_id=snapshot.snapshot_id,
                details_json=_dumps({
                    "snapshot_changed": True,
                    "fingerprint": fingerprint,
                }),
            ))
            return self._read_store._profile_payload(db, listing, snapshot)

    def _resolve_identity(
        self,
        db: Session,
        *,
        listing: Listing,
        source_profile_external_id: str | None,
        normalized_phone: str | None,
        image_hashes: list[int],
    ) -> tuple[int, MatchResult, int | None]:
        if listing.match_decision == "MANUAL_SPLIT":
            return (
                listing.person_id,
                MatchResult(
                    "MANUAL_SPLIT",
                    "Manuelle Trennung: automatisches Re-Merge gesperrt.",
                ),
                None,
            )

        candidates = db.scalars(
            select(Person).where(Person.person_id != listing.person_id)
        ).all()
        evaluated: list[tuple[int, MatchResult]] = []
        for person in candidates:
            source_ids = set(db.scalars(
                select(SourceProfile.external_profile_id).where(
                    SourceProfile.person_id == person.person_id,
                    SourceProfile.external_profile_id.is_not(None),
                )
            ).all())
            phones = set(db.scalars(
                select(PersonIdentifier.normalized_value).where(
                    PersonIdentifier.person_id == person.person_id,
                    PersonIdentifier.kind == "PHONE",
                )
            ).all())
            hash_values = set(db.scalars(
                select(PersonIdentifier.normalized_value).where(
                    PersonIdentifier.person_id == person.person_id,
                    PersonIdentifier.kind == "IMAGE_DHASH",
                )
            ).all())
            old_hashes = []
            for value in hash_values:
                try:
                    old_hashes.append(hash_from_hex(value))
                except ValueError:
                    pass
            match = match_identity(
                old_hashes,
                image_hashes,
                old_phones=phones,
                new_phone=normalized_phone,
                old_source_profile_ids=source_ids,
                new_source_profile_id=source_profile_external_id,
            )
            if match.decision != "NO_MATCH":
                evaluated.append((person.person_id, match))

        def rank(item: tuple[int, MatchResult]) -> tuple[int, int, int, int, int, int]:
            match = item[1]
            decision_rank = {
                "MATCH": 3,
                "STRONG_CANDIDATE": 2,
                "CANDIDATE": 1,
                "NO_MATCH": 0,
            }.get(match.decision, 0)
            return (
                decision_rank,
                int(match.source_profile_match),
                int(match.phone_match),
                match.strong_image_matches,
                match.very_strong_image_matches,
                -(match.best_distance if match.best_distance is not None else 999),
            )

        matches = sorted(
            [item for item in evaluated if item[1].decision == "MATCH"],
            key=rank,
            reverse=True,
        )
        if matches and (len(matches) == 1 or rank(matches[0]) > rank(matches[1])):
            return matches[0][0], matches[0][1], None
        weak = sorted(
            [
                item for item in evaluated
                if item[1].decision in {"STRONG_CANDIDATE", "CANDIDATE"}
            ],
            key=rank,
            reverse=True,
        )
        if weak:
            return listing.person_id, weak[0][1], weak[0][0]
        return (
            listing.person_id,
            MatchResult("NO_MATCH", "Keine ausreichende Identity-Evidence."),
            None,
        )

    @staticmethod
    def _upsert_source_profile(
        db: Session,
        person_id: int,
        profile: ProfileData,
        *,
        allow_reassign: bool = True,
    ) -> SourceProfile | None:
        if not profile.source_profile_id and not profile.canonical_id:
            return None
        row = None
        if profile.source_profile_id:
            row = db.scalar(select(SourceProfile).where(
                SourceProfile.source == "LADIES",
                SourceProfile.external_profile_id == profile.source_profile_id,
            ))
        if row is None and profile.canonical_id:
            row = db.scalar(select(SourceProfile).where(
                SourceProfile.source == "LADIES",
                SourceProfile.canonical_id == profile.canonical_id,
            ))
        if row is None:
            row = SourceProfile(
                person_id=person_id,
                source="LADIES",
                external_profile_id=profile.source_profile_id,
                canonical_id=profile.canonical_id,
                link_reason="Websiteeigene Profil-ID/Canonical-ID",
            )
            db.add(row)
            db.flush()
        else:
            if row.person_id != person_id and not allow_reassign:
                return None
            row.person_id = person_id
            row.external_profile_id = profile.source_profile_id or row.external_profile_id
            row.canonical_id = profile.canonical_id or row.canonical_id
            row.last_seen_at = utcnow()
        return row

    @staticmethod
    def _touch_identifier(
        db: Session,
        person_id: int,
        listing_id: int,
        kind: str,
        normalized: str | None,
        raw: str | None,
    ) -> None:
        if not normalized:
            return
        row = db.scalar(select(PersonIdentifier).where(
            PersonIdentifier.person_id == person_id,
            PersonIdentifier.kind == kind,
            PersonIdentifier.normalized_value == normalized,
        ))
        if row is None:
            db.add(PersonIdentifier(
                person_id=person_id,
                listing_id=listing_id,
                kind=kind,
                normalized_value=normalized,
                raw_value=raw,
            ))
        else:
            row.last_seen_at = utcnow()
            row.listing_id = listing_id
            if raw:
                row.raw_value = raw

    @staticmethod
    def _fingerprint_payload(
        profile: ProfileData,
        normalized_phone: str | None,
        archived_media: list[tuple[Any, ArchivedMedia]],
    ) -> dict[str, Any]:
        media = []
        for ref, archived in archived_media:
            media.append({
                "kind": ref.kind,
                "position": ref.position,
                "variant": ref.variant,
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

    @staticmethod
    def _add_snapshot_values(db: Session, snapshot: ListingSnapshot, profile: ProfileData) -> None:
        position = 0
        for group, values in profile.traits.items():
            for value in values:
                position += 1
                db.add(SnapshotValue(
                    snapshot_id=snapshot.snapshot_id,
                    kind="TRAIT",
                    group_name=group,
                    label=group,
                    value=value,
                    position=position,
                    provenance="DOM_VISIBLE",
                    confidence_rank=100,
                ))
        position = 0
        for group, values in profile.services.items():
            for value in values:
                position += 1
                db.add(SnapshotValue(
                    snapshot_id=snapshot.snapshot_id,
                    kind="SERVICE",
                    group_name=group,
                    label=group,
                    value=value,
                    position=position,
                    provenance="DOM_VISIBLE",
                    confidence_rank=100,
                ))
        position = 0
        for day, value in profile.opening_times.items():
            position += 1
            db.add(SnapshotValue(
                snapshot_id=snapshot.snapshot_id,
                kind="OPENING",
                group_name="Öffnungszeiten",
                label=day,
                value=value,
                position=position,
                provenance="DOM_VISIBLE_OR_MICRODATA",
                confidence_rank=95,
            ))
        if profile.hidden_location:
            db.add(SnapshotValue(
                snapshot_id=snapshot.snapshot_id,
                kind="PROVENANCE",
                group_name="Hidden",
                label="data-ort",
                value=profile.hidden_location,
                position=1,
                provenance="DATA_ATTRIBUTE",
                confidence_rank=40,
            ))

    @staticmethod
    def _add_snapshot_media(
        db: Session,
        snapshot: ListingSnapshot,
        archived_media: list[tuple[Any, ArchivedMedia]],
    ) -> None:
        for ref, archived in archived_media:
            db.add(SnapshotMedia(
                snapshot_id=snapshot.snapshot_id,
                media_asset_id=(archived.asset.media_asset_id if archived.asset else None),
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

    def _create_change_events(
        self,
        db: Session,
        listing: Listing,
        old: ListingSnapshot,
        new: ListingSnapshot,
    ) -> None:
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
                    person_id=listing.person_id,
                    listing_id=listing.listing_id,
                    from_snapshot_id=old.snapshot_id,
                    to_snapshot_id=new.snapshot_id,
                    event_type="FIELD_CHANGED",
                    field_key=field,
                    old_json=_dumps(before),
                    new_json=_dumps(after),
                    importance=importance,
                ))
        for field, before_json, after_json in (
            ("traits", old.traits_json, new.traits_json),
            ("services", old.services_json, new.services_json),
            ("opening_times", old.opening_times_json, new.opening_times_json),
        ):
            before, after = _loads(before_json, {}), _loads(after_json, {})
            if before != after:
                db.add(ChangeEvent(
                    person_id=listing.person_id,
                    listing_id=listing.listing_id,
                    from_snapshot_id=old.snapshot_id,
                    to_snapshot_id=new.snapshot_id,
                    event_type="STRUCTURED_CHANGED",
                    field_key=field,
                    old_json=_dumps(before),
                    new_json=_dumps(after),
                    importance="HIGH" if field == "services" else "MEDIUM",
                ))
        old_media = self._snapshot_media_signature(db, old.snapshot_id)
        new_media = self._snapshot_media_signature(db, new.snapshot_id)
        added = [item for item in new_media if item not in old_media]
        removed = [item for item in old_media if item not in new_media]
        if added or removed:
            db.add(ChangeEvent(
                person_id=listing.person_id,
                listing_id=listing.listing_id,
                from_snapshot_id=old.snapshot_id,
                to_snapshot_id=new.snapshot_id,
                event_type="MEDIA_CHANGED",
                field_key="media",
                importance="HIGH",
                old_json=_dumps({"removed": removed}),
                new_json=_dumps({"added": added}),
            ))

    @staticmethod
    def _snapshot_media_signature(db: Session, snapshot_id: int) -> list[dict[str, Any]]:
        rows = db.execute(
            select(SnapshotMedia, MediaAsset)
            .outerjoin(MediaAsset, MediaAsset.media_asset_id == SnapshotMedia.media_asset_id)
            .where(SnapshotMedia.snapshot_id == snapshot_id)
            .order_by(SnapshotMedia.position, SnapshotMedia.kind)
        ).all()
        return [
            {
                "kind": snapshot_media.kind,
                "sha256": asset.sha256 if asset else None,
                "base": snapshot_media.remote_base_url,
            }
            for snapshot_media, asset in rows
        ]

    @staticmethod
    def _record_identity_evidence(
        db: Session,
        listing: Listing,
        result: MatchResult,
        source_profile_id: str | None,
        phone: str | None,
    ) -> None:
        if source_profile_id:
            db.add(IdentityEvidence(
                listing_id=listing.listing_id,
                person_id=listing.person_id,
                candidate_person_id=listing.candidate_person_id,
                kind="SOURCE_PROFILE",
                value=source_profile_id,
                score=1.0 if result.source_profile_match else 0.5,
            ))
        if phone:
            db.add(IdentityEvidence(
                listing_id=listing.listing_id,
                person_id=listing.person_id,
                candidate_person_id=listing.candidate_person_id,
                kind="PHONE",
                value=phone,
                score=1.0 if result.phone_match else 0.5,
            ))
        if result.best_distance is not None:
            db.add(IdentityEvidence(
                listing_id=listing.listing_id,
                person_id=listing.person_id,
                candidate_person_id=listing.candidate_person_id,
                kind="IMAGE_DHASH",
                value=result.reason,
                best_distance=result.best_distance,
                score=max(0.0, 1.0 - result.best_distance / 16.0),
            ))

    def update_listing_status(
        self,
        external_listing_id: str,
        *,
        status: str,
        http_status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = str(status).upper()
        if status not in {"ACTIVE", "UNAVAILABLE", "UNKNOWN"}:
            raise ValueError("Ungültiger Status")
        now = utcnow()
        with self.database.session() as db:
            listing = db.scalar(
                select(Listing).where(Listing.external_listing_id == str(external_listing_id))
            )
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
            db.add(ListingObservation(
                listing_id=listing.listing_id,
                observed_at=now,
                source="STATUS",
                status=status,
                http_status=http_status,
                details_json=_dumps(details or {}),
            ))
            if previous != status:
                db.add(ChangeEvent(
                    person_id=listing.person_id,
                    listing_id=listing.listing_id,
                    event_type="STATUS_CHANGED",
                    field_key="status",
                    old_json=_dumps(previous),
                    new_json=_dumps(status),
                    importance="HIGH",
                ))
            return {
                "advert_id": external_listing_id,
                "status": status,
                "previous": previous,
                "person_id": listing.person_id,
            }

    def listing_detail(self, advert_id):
        return self._read_store.listing_detail(advert_id)

    def person_detail(self, person_id):
        return self._read_store.person_detail(person_id)

    def set_user_state(self, person_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        data = payload or {}
        with self.database.session() as db:
            person = db.get(Person, int(person_id))
            if person is None:
                raise KeyError(person_id)
            state = db.get(PersonUserState, person.person_id)
            if state is None:
                state = PersonUserState(person_id=person.person_id)
                db.add(state)
            if "preference" in data:
                preference = str(data.get("preference") or "NONE").upper()
                if preference not in {"NONE", "LIKE", "DISLIKE"}:
                    raise ValueError("Ungültige preference")
                state.preference = preference
            if "favorite" in data:
                state.favorite = bool(data.get("favorite"))
            if "repeat_interest" in data:
                state.repeat_interest = str(data.get("repeat_interest") or "UNKNOWN")[:32]
            if "private_note" in data:
                state.private_note = str(data.get("private_note") or "")[:10000] or None
            state.updated_at = utcnow()
            db.flush()
            return self._state_payload(state)

    def add_price_list(self, person_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        data = payload or {}
        items = list(data.get("items") or [])
        if not items:
            raise ValueError("Mindestens ein Preiseintrag ist erforderlich")
        with self.database.session() as db:
            if db.get(Person, int(person_id)) is None:
                raise KeyError(person_id)
            observed = parse_iso_datetime(data.get("observed_at")) or utcnow()
            listing_id = data.get("listing_id")
            row = PriceList(
                person_id=int(person_id),
                listing_id=int(listing_id) if listing_id else None,
                observed_at=observed,
                source=str(data.get("source") or "USER_ENTERED")[:32],
                currency="EUR",
                note=str(data.get("note") or "")[:4000] or None,
            )
            db.add(row)
            db.flush()
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
                    duration_minutes=(
                        int(item["duration_minutes"])
                        if item.get("duration_minutes") not in {None, ""} else None
                    ),
                    service=str(item.get("service") or "")[:256] or None,
                    amount_cents=amount,
                    note=str(item.get("note") or "")[:2000] or None,
                ))
            db.flush()
            return self._price_list_payload(db, row)

    def add_encounter(self, person_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        data = payload or {}
        with self.database.session() as db:
            if db.get(Person, int(person_id)) is None:
                raise KeyError(person_id)
            occurred = parse_iso_datetime(data.get("occurred_at")) or utcnow()

            def cents(name: str) -> int | None:
                value = data.get(name)
                if value in {None, ""}:
                    return None
                return round(float(value) * 100) if name.endswith("_eur") else int(value)

            row = Encounter(
                person_id=int(person_id),
                listing_id=int(data["listing_id"]) if data.get("listing_id") else None,
                occurred_at=occurred,
                duration_minutes=(
                    int(data["duration_minutes"])
                    if data.get("duration_minutes") not in {None, ""} else None
                ),
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
            db.add(row)
            db.flush()
            for item in list(data.get("services") or []):
                db.add(EncounterService(
                    encounter_id=row.encounter_id,
                    service_name=str(item.get("service_name") or item.get("name") or "")[:256],
                    amount_cents=(
                        round(float(item.get("amount_eur")) * 100)
                        if item.get("amount_eur") not in {None, ""} else None
                    ),
                    performed=bool(item.get("performed", True)),
                    note=str(item.get("note") or "")[:2000] or None,
                ))
            db.flush()
            return self._encounter_payload(db, row)

    def update_encounter(
        self,
        person_id: int,
        encounter_id: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        data = payload or {}

        def cents(key: str) -> int | None:
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
                row.duration_minutes = (
                    int(data["duration_minutes"])
                    if data.get("duration_minutes") not in {None, ""} else None
                )
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
                db.execute(delete(EncounterService).where(
                    EncounterService.encounter_id == row.encounter_id
                ))
                for item in list(data.get("services") or []):
                    name = str(item.get("service_name") or item.get("name") or "")[:256]
                    if not name:
                        continue
                    db.add(EncounterService(
                        encounter_id=row.encounter_id,
                        service_name=name,
                        amount_cents=(
                            round(float(item.get("amount_eur")) * 100)
                            if item.get("amount_eur") not in {None, ""} else None
                        ),
                        performed=bool(item.get("performed", True)),
                        note=str(item.get("note") or "")[:2000] or None,
                    ))
            db.flush()
            return self._encounter_payload(db, row)

    def lists(self, tab):
        return self._read_store.lists(tab)

    def status_targets(self) -> list[dict[str, Any]]:
        with self.database.session() as db:
            rows = db.scalars(
                select(Listing)
                .join(PersonUserState, PersonUserState.person_id == Listing.person_id)
                .where(or_(
                    PersonUserState.preference != "NONE",
                    PersonUserState.favorite.is_(True),
                ))
                .order_by(desc(Listing.last_seen_at))
            ).all()
            return [
                {
                    "advert_id": row.external_listing_id,
                    "profile_url": row.current_url,
                    "person_id": row.person_id,
                    "status": row.current_status,
                }
                for row in rows
            ]

    def merge_persons(self, source_person_id: int, target_person_id: int):
        if int(source_person_id) == int(target_person_id):
            return self.person_detail(target_person_id)
        with self.database.session() as db:
            self._merge_persons_in_session(
                db,
                source_person_id=int(source_person_id),
                target_person_id=int(target_person_id),
                action="MANUAL_MERGE",
            )
        return self.person_detail(target_person_id)

    def _merge_persons_in_session(
        self,
        db: Session,
        *,
        source_person_id: int,
        target_person_id: int,
        action: str,
    ) -> None:
        source = db.get(Person, source_person_id)
        target = db.get(Person, target_person_id)
        if source is None or target is None or source_person_id == target_person_id:
            return
        source_state = db.get(PersonUserState, source_person_id)
        target_state = db.get(PersonUserState, target_person_id)
        if target_state is None:
            target_state = PersonUserState(person_id=target_person_id)
            db.add(target_state)
            db.flush()
        if source_state:
            if target_state.preference == "NONE" and source_state.preference != "NONE":
                target_state.preference = source_state.preference
            target_state.favorite = target_state.favorite or source_state.favorite
            if (
                target_state.repeat_interest == "UNKNOWN"
                and source_state.repeat_interest != "UNKNOWN"
            ):
                target_state.repeat_interest = source_state.repeat_interest
            if source_state.private_note:
                target_state.private_note = (
                    (target_state.private_note or "")
                    + ("\n\n" if target_state.private_note else "")
                    + source_state.private_note
                )[:10000]

        listings = db.scalars(
            select(Listing).where(Listing.person_id == source_person_id)
        ).all()
        for listing in listings:
            db.add(IdentityEvent(
                listing_id=listing.listing_id,
                previous_person_id=source_person_id,
                new_person_id=target_person_id,
                action=action,
                evidence_json=_dumps({"reason": listing.match_reason}),
            ))
            listing.person_id = target_person_id
        for listing in db.scalars(
            select(Listing).where(Listing.candidate_person_id == source_person_id)
        ).all():
            listing.candidate_person_id = target_person_id
        for row in db.scalars(
            select(SourceProfile).where(SourceProfile.person_id == source_person_id)
        ).all():
            duplicate = None
            if row.external_profile_id:
                duplicate = db.scalar(select(SourceProfile).where(
                    SourceProfile.person_id == target_person_id,
                    SourceProfile.source == row.source,
                    SourceProfile.external_profile_id == row.external_profile_id,
                ))
            if duplicate:
                for listing in db.scalars(
                    select(Listing).where(Listing.source_profile_id == row.source_profile_id)
                ).all():
                    listing.source_profile_id = duplicate.source_profile_id
                db.delete(row)
            else:
                row.person_id = target_person_id
        for row in db.scalars(
            select(PersonIdentifier).where(PersonIdentifier.person_id == source_person_id)
        ).all():
            duplicate = db.scalar(select(PersonIdentifier).where(
                PersonIdentifier.person_id == target_person_id,
                PersonIdentifier.kind == row.kind,
                PersonIdentifier.normalized_value == row.normalized_value,
            ))
            if duplicate:
                duplicate.last_seen_at = max(duplicate.last_seen_at, row.last_seen_at)
                db.delete(row)
            else:
                row.person_id = target_person_id
        for model in (PriceList, Encounter, ChangeEvent, IdentityEvidence):
            db.execute(
                update(model)
                .where(model.person_id == source_person_id)
                .values(person_id=target_person_id)
            )
        if source_state:
            db.delete(source_state)
        target.first_seen_at = min(target.first_seen_at, source.first_seen_at)
        target.last_seen_at = max(target.last_seen_at, source.last_seen_at)
        target.updated_at = utcnow()
        if not target.display_name and source.display_name:
            target.display_name = source.display_name
        db.delete(source)
        db.flush()

    def split_listing(self, listing_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            listing = db.get(Listing, int(listing_id))
            if listing is None:
                raise KeyError(listing_id)
            old_person_id = listing.person_id
            snapshot = (
                db.get(ListingSnapshot, listing.current_snapshot_id)
                if listing.current_snapshot_id else None
            )
            new_person = Person(
                display_name=snapshot.name if snapshot else None,
                first_seen_at=listing.first_seen_at,
                last_seen_at=listing.last_seen_at,
            )
            db.add(new_person)
            db.flush()
            db.add(PersonUserState(person_id=new_person.person_id))
            listing.person_id = new_person.person_id
            listing.match_decision = "MANUAL_SPLIT"
            listing.candidate_person_id = None
            db.add(IdentityEvent(
                listing_id=listing.listing_id,
                previous_person_id=old_person_id,
                new_person_id=new_person.person_id,
                action="MANUAL_SPLIT",
            ))
            for identifier in db.scalars(
                select(PersonIdentifier).where(
                    PersonIdentifier.listing_id == listing.listing_id
                )
            ).all():
                identifier.person_id = new_person.person_id
            if listing.source_profile_id:
                source_profile = db.get(SourceProfile, listing.source_profile_id)
                linked_count = (
                    db.scalar(select(func.count(Listing.listing_id)).where(
                        Listing.source_profile_id == source_profile.source_profile_id
                    )) if source_profile else 0
                )
                if source_profile and linked_count == 1:
                    source_profile.person_id = new_person.person_id
                elif source_profile:
                    listing.source_profile_id = None
            db.flush()
            return {
                "listing_id": listing.listing_id,
                "old_person_id": old_person_id,
                "new_person_id": new_person.person_id,
            }

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
    def _price_list_payload(db, row: PriceList) -> dict[str, Any]:
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
    def _encounter_payload(db, row: Encounter) -> dict[str, Any]:
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
            "base_price_eur": (
                row.base_price_cents / 100 if row.base_price_cents is not None else None
            ),
            "total_price_eur": (
                row.total_price_cents / 100 if row.total_price_cents is not None else None
            ),
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
                    "amount_eur": (
                        item.amount_cents / 100 if item.amount_cents is not None else None
                    ),
                    "performed": item.performed,
                    "note": item.note,
                }
                for item in items
            ],
        }
