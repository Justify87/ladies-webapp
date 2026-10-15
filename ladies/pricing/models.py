from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ladies_v9.models import Base, utcnow
from ladies_v9.messaging import models as _messaging_models  # noqa: F401


class ChatMessageDerived(Base):
    __tablename__ = "chat_message_derived"

    message_id: Mapped[int] = mapped_column(
        ForeignKey("chat_messages.message_id", ondelete="CASCADE"), primary_key=True
    )
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    ocr_status: Mapped[str] = mapped_column(String(24), default="NOT_NEEDED", nullable=False)
    ocr_text: Mapped[Optional[str]] = mapped_column(Text)
    ocr_engine: Mapped[Optional[str]] = mapped_column(String(64))
    ocr_engine_version: Mapped[Optional[str]] = mapped_column(String(64))
    ocr_confidence: Mapped[Optional[float]] = mapped_column(Float)
    price_signal_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    price_signal_reasons_json: Mapped[Optional[str]] = mapped_column(Text)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PriceExtraction(Base):
    __tablename__ = "price_extractions"
    __table_args__ = (
        Index("idx_price_extraction_person_time", "person_id", "created_at"),
        Index("idx_price_extraction_thread_time", "thread_id", "created_at"),
        Index("idx_price_extraction_status", "status"),
    )

    price_extraction_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[Optional[int]] = mapped_column(ForeignKey("persons.person_id", ondelete="SET NULL"))
    thread_id: Mapped[Optional[int]] = mapped_column(ForeignKey("chat_threads.thread_id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(24), default="AUTO", nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    extractor_name: Mapped[str] = mapped_column(String(64), default="rules", nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(64), default="1", nullable=False)
    original_proposal_json: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_proposal_json: Mapped[Optional[str]] = mapped_column(Text)
    context_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    base_price_list_id: Mapped[Optional[int]] = mapped_column(ForeignKey("price_lists.price_list_id", ondelete="SET NULL"))
    accepted_price_list_id: Mapped[Optional[int]] = mapped_column(ForeignKey("price_lists.price_list_id", ondelete="SET NULL"))
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class PriceExtractionLocation(Base):
    """Location scope chosen for one extraction without mutating the extraction schema."""

    __tablename__ = "price_extraction_locations"

    price_extraction_id: Mapped[int] = mapped_column(
        ForeignKey("price_extractions.price_extraction_id", ondelete="CASCADE"), primary_key=True
    )
    suggested_listing_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("listings.listing_id", ondelete="SET NULL")
    )
    selected_listing_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("listings.listing_id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PriceExtractionMessage(Base):
    __tablename__ = "price_extraction_messages"
    __table_args__ = (
        UniqueConstraint("price_extraction_id", "message_id", name="uq_price_extraction_message"),
        Index("idx_price_extraction_message_id", "message_id"),
    )

    price_extraction_message_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    price_extraction_id: Mapped[int] = mapped_column(
        ForeignKey("price_extractions.price_extraction_id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.message_id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(24), default="CONTEXT", nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PriceListRevisionMeta(Base):
    __tablename__ = "price_list_revision_meta"

    price_list_id: Mapped[int] = mapped_column(
        ForeignKey("price_lists.price_list_id", ondelete="CASCADE"), primary_key=True
    )
    supersedes_price_list_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("price_lists.price_list_id", ondelete="SET NULL")
    )
    source_extraction_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("price_extractions.price_extraction_id", ondelete="SET NULL")
    )
    revision_type: Mapped[str] = mapped_column(String(24), default="FULL", nullable=False)
    completeness: Mapped[str] = mapped_column(String(24), default="FULL", nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PriceListLocationSnapshot(Base):
    """Immutable display/location snapshot for one accepted price revision."""

    __tablename__ = "price_list_location_snapshots"

    price_list_id: Mapped[int] = mapped_column(
        ForeignKey("price_lists.price_list_id", ondelete="CASCADE"), primary_key=True
    )
    listing_id: Mapped[Optional[int]] = mapped_column(ForeignKey("listings.listing_id", ondelete="SET NULL"))
    listing_snapshot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("listing_snapshots.snapshot_id", ondelete="SET NULL")
    )
    label: Mapped[Optional[str]] = mapped_column(String(512))
    city: Mapped[Optional[str]] = mapped_column(String(160))
    postal_code: Mapped[Optional[str]] = mapped_column(String(32))
    address: Mapped[Optional[str]] = mapped_column(String(160))
    external_listing_id: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PriceOfferMeta(Base):
    __tablename__ = "price_offer_meta"

    price_item_id: Mapped[int] = mapped_column(
        ForeignKey("price_items.price_item_id", ondelete="CASCADE"), primary_key=True
    )
    duration_max_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    amount_type: Mapped[str] = mapped_column(String(24), default="EXACT", nullable=False)
    amount_max_cents: Mapped[Optional[int]] = mapped_column(Integer)


class PriceServiceRule(Base):
    __tablename__ = "price_service_rules"
    __table_args__ = (Index("idx_price_service_rule_list", "price_list_id", "position"),)

    price_service_rule_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    price_list_id: Mapped[int] = mapped_column(ForeignKey("price_lists.price_list_id", ondelete="CASCADE"), nullable=False)
    service_key: Mapped[Optional[str]] = mapped_column(String(96))
    raw_label: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False)
    amount_cents: Mapped[Optional[int]] = mapped_column(Integer)
    note: Mapped[Optional[str]] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PriceCondition(Base):
    __tablename__ = "price_conditions"
    __table_args__ = (Index("idx_price_condition_list", "price_list_id", "position"),)

    price_condition_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    price_list_id: Mapped[int] = mapped_column(ForeignKey("price_lists.price_list_id", ondelete="CASCADE"), nullable=False)
    condition_type: Mapped[str] = mapped_column(String(32), default="OTHER", nullable=False)
    label: Mapped[str] = mapped_column(String(512), nullable=False)
    amount_cents: Mapped[Optional[int]] = mapped_column(Integer)
    note: Mapped[Optional[str]] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PriceEvidence(Base):
    __tablename__ = "price_evidence"
    __table_args__ = (
        Index("idx_price_evidence_list", "price_list_id"),
        Index("idx_price_evidence_message", "message_id"),
    )

    price_evidence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    price_list_id: Mapped[int] = mapped_column(ForeignKey("price_lists.price_list_id", ondelete="CASCADE"), nullable=False)
    price_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey("price_items.price_item_id", ondelete="CASCADE"))
    price_service_rule_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("price_service_rules.price_service_rule_id", ondelete="CASCADE")
    )
    price_condition_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("price_conditions.price_condition_id", ondelete="CASCADE")
    )
    message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("chat_messages.message_id", ondelete="SET NULL"))
    extraction_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("price_extractions.price_extraction_id", ondelete="SET NULL")
    )
    source_type: Mapped[str] = mapped_column(String(32), default="CHAT_TEXT", nullable=False)
    excerpt: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)