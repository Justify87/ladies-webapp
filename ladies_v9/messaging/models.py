from __future__ import annotations
from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from ladies_v9.models import Base, utcnow


class ContactEndpoint(Base):
    __tablename__ = "contact_endpoints"
    __table_args__ = (
        UniqueConstraint("provider", "normalized_phone", name="uq_contact_endpoint_provider_phone"),
        Index("idx_contact_endpoint_person", "person_id"),
    )
    endpoint_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Deliberately not a DB FK: the existing V9 merge deletes source Person in its
    # transaction. Keeping the source id until IdentityEvent reconciliation lets
    # messaging move endpoints/threads losslessly to the canonical target person.
    person_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(24), default="WHATSAPP", nullable=False)
    normalized_phone: Mapped[str] = mapped_column(String(64), nullable=False)
    display_phone: Mapped[Optional[str]] = mapped_column(String(128))
    provider_address: Mapped[Optional[str]] = mapped_column(String(256))
    source_listing_id: Mapped[Optional[int]] = mapped_column(ForeignKey("listings.listing_id", ondelete="SET NULL"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    is_preferred: Mapped[bool] = mapped_column(default=False, nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ChatThread(Base):
    __tablename__ = "chat_threads"
    __table_args__ = (
        UniqueConstraint("person_id", name="uq_chat_thread_person"),
        Index("idx_chat_thread_activity", "last_message_at"),
    )
    thread_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # See ContactEndpoint.person_id: logical reference is required for safe replay
    # of the repository-owned IdentityEvent merge path.
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
