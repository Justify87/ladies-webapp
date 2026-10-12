from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


LOGGER = logging.getLogger("ladies_explorer.models")

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Person(Base):
    __tablename__ = "persons"

    person_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(160))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    listings: Mapped[list["Listing"]] = relationship(back_populates="person", foreign_keys="Listing.person_id")
    source_profiles: Mapped[list["SourceProfile"]] = relationship(back_populates="person")
    user_state: Mapped[Optional["PersonUserState"]] = relationship(back_populates="person", uselist=False)


class PersonUserState(Base):
    __tablename__ = "person_user_state"
    __table_args__ = (
        CheckConstraint("preference IN ('NONE','LIKE','DISLIKE')", name="ck_person_pref"),
    )

    person_id: Mapped[int] = mapped_column(ForeignKey("persons.person_id", ondelete="CASCADE"), primary_key=True)
    preference: Mapped[str] = mapped_column(String(16), default="NONE", nullable=False)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    repeat_interest: Mapped[str] = mapped_column(String(32), default="UNKNOWN", nullable=False)
    private_note: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    person: Mapped[Person] = relationship(back_populates="user_state")


class SourceProfile(Base):
    __tablename__ = "source_profiles"
    __table_args__ = (
        UniqueConstraint("source", "external_profile_id", name="uq_source_profile_external"),
        Index("idx_source_profile_person", "person_id"),
        Index("idx_source_profile_canonical", "canonical_id"),
    )

    source_profile_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.person_id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="LADIES", nullable=False)
    external_profile_id: Mapped[Optional[str]] = mapped_column(String(128))
    canonical_id: Mapped[Optional[str]] = mapped_column(String(256))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    link_reason: Mapped[Optional[str]] = mapped_column(String(256))

    person: Mapped[Person] = relationship(back_populates="source_profiles")
    listings: Mapped[list["Listing"]] = relationship(back_populates="source_profile")


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "external_listing_id", name="uq_listing_external"),
        Index("idx_listing_person", "person_id"),
        Index("idx_listing_source_profile", "source_profile_id"),
        Index("idx_listing_status", "current_status"),
    )

    listing_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.person_id", ondelete="CASCADE"), nullable=False)
    source_profile_id: Mapped[Optional[int]] = mapped_column(ForeignKey("source_profiles.source_profile_id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(32), default="LADIES", nullable=False)
    external_listing_id: Mapped[str] = mapped_column(String(64), nullable=False)
    current_url: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(String(128))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    current_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    status_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    current_snapshot_id: Mapped[Optional[int]] = mapped_column(Integer)

    match_decision: Mapped[str] = mapped_column(String(32), default="NO_MATCH", nullable=False)
    candidate_person_id: Mapped[Optional[int]] = mapped_column(ForeignKey("persons.person_id", ondelete="SET NULL"))
    match_reason: Mapped[Optional[str]] = mapped_column(Text)
    match_best_distance: Mapped[Optional[int]] = mapped_column(Integer)
    match_image_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    match_strong_image_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    match_phone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    match_source_profile: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    person: Mapped[Person] = relationship(back_populates="listings", foreign_keys=[person_id])
    source_profile: Mapped[Optional[SourceProfile]] = relationship(back_populates="listings")
    snapshots: Mapped[list["ListingSnapshot"]] = relationship(back_populates="listing", cascade="all, delete-orphan")


class SearchRun(Base):
    __tablename__ = "search_runs"

    search_run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    city: Mapped[Optional[str]] = mapped_column(String(160))
    city_slug: Mapped[Optional[str]] = mapped_column(String(160))
    radius_km: Mapped[Optional[int]] = mapped_column(Integer)
    filters_json: Mapped[Optional[str]] = mapped_column(Text)
    advert_count: Mapped[Optional[int]] = mapped_column(Integer)
    distance_searched: Mapped[Optional[int]] = mapped_column(Integer)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text)


class SearchResult(Base):
    __tablename__ = "search_results"
    __table_args__ = (
        UniqueConstraint("search_run_id", "listing_id", name="uq_search_result_listing"),
        Index("idx_search_result_listing", "listing_id"),
    )

    search_result_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    search_run_id: Mapped[int] = mapped_column(ForeignKey("search_runs.search_run_id", ondelete="CASCADE"), nullable=False)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.listing_id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(Text)
    name: Mapped[Optional[str]] = mapped_column(String(160))
    city: Mapped[Optional[str]] = mapped_column(String(160))
    has_video: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_ai_content: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    markers_json: Mapped[Optional[str]] = mapped_column(Text)


class ListingObservation(Base):
    __tablename__ = "listing_observations"
    __table_args__ = (
        Index("idx_observation_listing_time", "listing_id", "observed_at"),
    )

    observation_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.listing_id", ondelete="CASCADE"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="SEEN", nullable=False)
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    search_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("search_runs.search_run_id", ondelete="SET NULL"))
    snapshot_id: Mapped[Optional[int]] = mapped_column(Integer)
    details_json: Mapped[Optional[str]] = mapped_column(Text)


class ListingSnapshot(Base):
    __tablename__ = "listing_snapshots"
    __table_args__ = (
        UniqueConstraint("listing_id", "fingerprint", name="uq_listing_snapshot_fingerprint"),
        Index("idx_snapshot_listing_time", "listing_id", "captured_at"),
    )

    snapshot_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.listing_id", ondelete="CASCADE"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    name: Mapped[Optional[str]] = mapped_column(String(160))
    city: Mapped[Optional[str]] = mapped_column(String(160))
    postal_code: Mapped[Optional[str]] = mapped_column(String(32))
    hidden_location: Mapped[Optional[str]] = mapped_column(String(160))
    category: Mapped[Optional[str]] = mapped_column(String(128))
    description: Mapped[Optional[str]] = mapped_column(Text)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    external_profile_id: Mapped[Optional[str]] = mapped_column(String(128))
    canonical_id: Mapped[Optional[str]] = mapped_column(String(256))
    phone_display: Mapped[Optional[str]] = mapped_column(String(128))
    normalized_phone: Mapped[Optional[str]] = mapped_column(String(64))
    whatsapp_url: Mapped[Optional[str]] = mapped_column(Text)
    sms_url: Mapped[Optional[str]] = mapped_column(Text)
    tel_url: Mapped[Optional[str]] = mapped_column(Text)
    traits_json: Mapped[Optional[str]] = mapped_column(Text)
    services_json: Mapped[Optional[str]] = mapped_column(Text)
    opening_times_json: Mapped[Optional[str]] = mapped_column(Text)
    auxiliary_json: Mapped[Optional[str]] = mapped_column(Text)
    raw_html_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    raw_html_path: Mapped[Optional[str]] = mapped_column(Text)

    listing: Mapped[Listing] = relationship(back_populates="snapshots")
    values: Mapped[list["SnapshotValue"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")
    media: Mapped[list["SnapshotMedia"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")


class SnapshotValue(Base):
    __tablename__ = "snapshot_values"
    __table_args__ = (
        Index("idx_snapshot_value_lookup", "kind", "group_name", "label", "value"),
    )

    snapshot_value_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("listing_snapshots.snapshot_id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    group_name: Mapped[Optional[str]] = mapped_column(String(160))
    label: Mapped[Optional[str]] = mapped_column(String(160))
    value: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), default="DOM_VISIBLE", nullable=False)
    confidence_rank: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    snapshot: Mapped[ListingSnapshot] = relationship(back_populates="values")


class PersonIdentifier(Base):
    __tablename__ = "person_identifiers"
    __table_args__ = (
        UniqueConstraint("person_id", "kind", "normalized_value", name="uq_person_identifier"),
        Index("idx_identifier_kind_value", "kind", "normalized_value"),
    )

    identifier_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.person_id", ondelete="CASCADE"), nullable=False)
    listing_id: Mapped[Optional[int]] = mapped_column(ForeignKey("listings.listing_id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(512), nullable=False)
    raw_value: Mapped[Optional[str]] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        Index("idx_media_dhash", "dhash"),
    )

    media_asset_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    dhash: Mapped[Optional[str]] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    mime_type: Mapped[Optional[str]] = mapped_column(String(128))
    byte_size: Mapped[Optional[int]] = mapped_column(Integer)
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class MediaSource(Base):
    __tablename__ = "media_sources"
    __table_args__ = (
        UniqueConstraint("remote_base_url", name="uq_media_source_base_url"),
    )

    media_source_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    remote_base_url: Mapped[str] = mapped_column(Text, nullable=False)
    last_remote_url: Mapped[Optional[str]] = mapped_column(Text)
    revision_token: Mapped[Optional[str]] = mapped_column(String(256))
    etag: Mapped[Optional[str]] = mapped_column(String(256))
    last_modified: Mapped[Optional[str]] = mapped_column(String(256))
    content_length: Mapped[Optional[int]] = mapped_column(Integer)
    media_asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("media_assets.media_asset_id", ondelete="SET NULL"))
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class SnapshotMedia(Base):
    __tablename__ = "snapshot_media"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "position", "kind", name="uq_snapshot_media_position"),
        Index("idx_snapshot_media_asset", "media_asset_id"),
    )

    snapshot_media_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("listing_snapshots.snapshot_id", ondelete="CASCADE"), nullable=False)
    media_asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("media_assets.media_asset_id", ondelete="SET NULL"))
    media_source_id: Mapped[Optional[int]] = mapped_column(ForeignKey("media_sources.media_source_id", ondelete="SET NULL"))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="GALLERY", nullable=False)
    remote_url: Mapped[str] = mapped_column(Text, nullable=False)
    remote_base_url: Mapped[Optional[str]] = mapped_column(Text)
    remote_variant: Mapped[Optional[str]] = mapped_column(String(32))
    revision_token: Mapped[Optional[str]] = mapped_column(String(256))
    etag: Mapped[Optional[str]] = mapped_column(String(256))
    last_modified: Mapped[Optional[str]] = mapped_column(String(256))

    snapshot: Mapped[ListingSnapshot] = relationship(back_populates="media")


class ChangeEvent(Base):
    __tablename__ = "change_events"
    __table_args__ = (
        Index("idx_change_person_time", "person_id", "created_at"),
        Index("idx_change_listing_time", "listing_id", "created_at"),
    )

    change_event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.person_id", ondelete="CASCADE"), nullable=False)
    listing_id: Mapped[Optional[int]] = mapped_column(ForeignKey("listings.listing_id", ondelete="CASCADE"))
    from_snapshot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("listing_snapshots.snapshot_id", ondelete="SET NULL"))
    to_snapshot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("listing_snapshots.snapshot_id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    field_key: Mapped[Optional[str]] = mapped_column(String(160))
    old_json: Mapped[Optional[str]] = mapped_column(Text)
    new_json: Mapped[Optional[str]] = mapped_column(Text)
    importance: Mapped[str] = mapped_column(String(16), default="MEDIUM", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class IdentityEvidence(Base):
    __tablename__ = "identity_evidence"
    __table_args__ = (
        Index("idx_identity_evidence_listing", "listing_id"),
    )

    identity_evidence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.listing_id", ondelete="CASCADE"), nullable=False)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.person_id", ondelete="CASCADE"), nullable=False)
    candidate_person_id: Mapped[Optional[int]] = mapped_column(ForeignKey("persons.person_id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[Optional[str]] = mapped_column(Text)
    score: Mapped[Optional[float]] = mapped_column(Float)
    best_distance: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class IdentityEvent(Base):
    __tablename__ = "identity_events"

    identity_event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.listing_id", ondelete="CASCADE"), nullable=False)
    previous_person_id: Mapped[Optional[int]] = mapped_column(ForeignKey("persons.person_id", ondelete="SET NULL"))
    new_person_id: Mapped[int] = mapped_column(ForeignKey("persons.person_id", ondelete="CASCADE"), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PriceList(Base):
    __tablename__ = "price_lists"
    __table_args__ = (Index("idx_price_list_person_time", "person_id", "observed_at"),)

    price_list_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.person_id", ondelete="CASCADE"), nullable=False)
    listing_id: Mapped[Optional[int]] = mapped_column(ForeignKey("listings.listing_id", ondelete="SET NULL"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="USER_ENTERED", nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="EUR", nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    items: Mapped[list["PriceItem"]] = relationship(back_populates="price_list", cascade="all, delete-orphan")


class PriceItem(Base):
    __tablename__ = "price_items"

    price_item_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    price_list_id: Mapped[int] = mapped_column(ForeignKey("price_lists.price_list_id", ondelete="CASCADE"), nullable=False)
    item_type: Mapped[str] = mapped_column(String(24), default="BASE", nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(256))
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    service: Mapped[Optional[str]] = mapped_column(String(256))
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)

    price_list: Mapped[PriceList] = relationship(back_populates="items")


class Encounter(Base):
    __tablename__ = "encounters"
    __table_args__ = (
        CheckConstraint("overall_rating IS NULL OR (overall_rating BETWEEN 1 AND 5)", name="ck_encounter_overall"),
        CheckConstraint("sympathy_rating IS NULL OR (sympathy_rating BETWEEN 1 AND 5)", name="ck_encounter_sympathy"),
        Index("idx_encounter_person_time", "person_id", "occurred_at"),
    )

    encounter_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.person_id", ondelete="CASCADE"), nullable=False)
    listing_id: Mapped[Optional[int]] = mapped_column(ForeignKey("listings.listing_id", ondelete="SET NULL"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    base_price_cents: Mapped[Optional[int]] = mapped_column(Integer)
    total_price_cents: Mapped[Optional[int]] = mapped_column(Integer)
    overall_rating: Mapped[Optional[int]] = mapped_column(Integer)
    sympathy_rating: Mapped[Optional[int]] = mapped_column(Integer)
    photo_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    service_accuracy: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    hygiene: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    kissing: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    negotiable: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    services: Mapped[list["EncounterService"]] = relationship(back_populates="encounter", cascade="all, delete-orphan")


class EncounterService(Base):
    __tablename__ = "encounter_services"

    encounter_service_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("encounters.encounter_id", ondelete="CASCADE"), nullable=False)
    service_name: Mapped[str] = mapped_column(String(256), nullable=False)
    amount_cents: Mapped[Optional[int]] = mapped_column(Integer)
    performed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)

    encounter: Mapped[Encounter] = relationship(back_populates="services")


class VisualAnalysis(Base):
    __tablename__ = "visual_analyses"
    __table_args__ = (
        UniqueConstraint("media_asset_id", "engine", "engine_version", name="uq_visual_analysis_engine"),
    )

    visual_analysis_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    media_asset_id: Mapped[int] = mapped_column(ForeignKey("media_assets.media_asset_id", ondelete="CASCADE"), nullable=False)
    engine: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[Optional[float]] = mapped_column(Float)
    label: Mapped[Optional[str]] = mapped_column(String(64))
    reasons_json: Mapped[Optional[str]] = mapped_column(Text)
    diagnostics_json: Mapped[Optional[str]] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
