from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ladies_v9.models import Base, utcnow


class ContactEndpoint(Base):
    __tablename__ = "contact_endpoints"
    __table_args__ = (
        UniqueConstraint("provider", "normalized_phone", name="uq_contact_endpoint_provider_phone"),
        UniqueConstraint("provider", "provider_address", name="uq_contact_endpoint_provider_address"),
        Index("idx_contact_endpoint_person", "person_id"),
    )
    endpoint_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Person identity is reconciled through IdentityEvent, so this is a logical reference rather than a DB FK.
    person_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(24), default="WHATSAPP", nullable=False)
    # A provider endpoint may exist before a phone-number JID is available.
    normalized_phone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    display_phone: Mapped[Optional[str]] = mapped_column(String(128))
    provider_address: Mapped[Optional[str]] = mapped_column(String(256))
    source_listing_id: Mapped[Optional[int]] = mapped_column(ForeignKey("listings.listing_id", ondelete="SET NULL"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    is_preferred: Mapped[bool] = mapped_column(default=False, nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ProviderPeerState(Base):
    """Provider-owned contact/chat metadata kept outside canonical conversations.

    A provider peer may exist before a ContactEndpoint is resolved. Conversely, a
    local conversation must remain valid after the provider deletes its chat row.
    Keeping this state separate makes that ownership boundary explicit.
    """

    __tablename__ = "provider_peer_state"
    __table_args__ = (
        Index("idx_provider_peer_phone", "provider", "phone_address"),
    )
    provider: Mapped[str] = mapped_column(String(24), primary_key=True)
    provider_address: Mapped[str] = mapped_column(String(256), primary_key=True)
    phone_address: Mapped[Optional[str]] = mapped_column(String(256))
    display_name: Mapped[Optional[str]] = mapped_column(String(256))
    unread_count: Mapped[Optional[int]] = mapped_column(Integer)
    chat_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    state_json: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ChatThread(Base):
    __tablename__ = "chat_threads"
    __table_args__ = (
        UniqueConstraint("person_id", name="uq_chat_thread_person"),
        Index("idx_chat_thread_activity", "last_message_at"),
    )
    thread_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Uses the same logical person reference contract as ContactEndpoint.
    person_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    unread_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="thread", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("provider_message_id", name="uq_chat_message_provider_id"),
        Index("idx_chat_message_thread_time", "thread_id", "timestamp"),
    )
    message_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("chat_threads.thread_id", ondelete="CASCADE"), nullable=False)
    endpoint_id: Mapped[Optional[int]] = mapped_column(ForeignKey("contact_endpoints.endpoint_id", ondelete="SET NULL"))
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(256))
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    message_type: Mapped[str] = mapped_column(String(24), default="TEXT", nullable=False)
    text: Mapped[Optional[str]] = mapped_column(Text)
    media_json: Mapped[Optional[str]] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    provider_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text)
    reply_to_message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("chat_messages.message_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    thread: Mapped[ChatThread] = relationship(back_populates="messages")
    extra: Mapped[Optional["ChatMessageMeta"]] = relationship(
        back_populates="message", uselist=False, cascade="all, delete-orphan"
    )
    reactions: Mapped[list["ChatReaction"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class ChatMessageMeta(Base):
    """Provider message metadata stored separately from canonical message content."""

    __tablename__ = "chat_message_meta"
    message_id: Mapped[int] = mapped_column(
        ForeignKey("chat_messages.message_id", ondelete="CASCADE"), primary_key=True
    )
    provider_key_json: Mapped[Optional[str]] = mapped_column(Text)
    quote_provider_message_id: Mapped[Optional[str]] = mapped_column(String(256), index=True)
    quote_snapshot_json: Mapped[Optional[str]] = mapped_column(Text)
    read_by_me_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    read_receipt_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    view_once: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    view_once_consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    edited_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    message: Mapped[ChatMessage] = relationship(back_populates="extra")


class ChatReaction(Base):
    __tablename__ = "chat_reactions"
    __table_args__ = (
        UniqueConstraint("message_id", "actor", name="uq_chat_reaction_message_actor"),
        Index("idx_chat_reaction_message", "message_id"),
    )
    reaction_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("chat_messages.message_id", ondelete="CASCADE"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(16), nullable=False)  # ME / REMOTE
    emoji: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_key_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    message: Mapped[ChatMessage] = relationship(back_populates="reactions")


class ChatClassification(Base):
    """Sticky product classification for a persisted conversation.

    This state intentionally lives outside discovery/search state. Automatic
    reconciliation may promote UNKNOWN to LADIES_CONFIRMED, but it never
    overwrites an explicit LADIES_CONFIRMED or NON_LADIES decision.
    """

    __tablename__ = "chat_classifications"
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("chat_threads.thread_id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(48), default="bootstrap", nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class EndpointClassification(Base):
    """Sticky disposition for a WhatsApp endpoint across thread merges."""

    __tablename__ = "endpoint_classifications"
    endpoint_id: Mapped[int] = mapped_column(
        ForeignKey("contact_endpoints.endpoint_id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(24), default="UNKNOWN", nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(48), default="bootstrap", nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
