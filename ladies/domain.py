from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any


LOGGER = logging.getLogger("ladies_explorer.domain")

@dataclass(frozen=True)
class SearchProfile:
    advert_id: str
    name: str
    city: str
    category: str
    profile_url: str
    thumbnail_url: str | None
    has_video: bool = False
    is_premium: bool = False
    has_ai_content: bool = False
    markers: dict[str, Any] = field(default_factory=dict)


@dataclass
class MediaReference:
    url: str
    kind: str
    position: int
    variant: str | None = None
    revision_token: str | None = None
    role: str = "GALLERY"


@dataclass
class ProfileData:
    advert_id: str
    source_profile_id: str | None
    canonical_id: str | None
    name: str
    city: str
    postal_code: str | None
    hidden_location: str | None
    category: str
    source_url: str
    description: str
    phone_display: str | None = None
    whatsapp_url: str | None = None
    sms_url: str | None = None
    tel_url: str | None = None
    traits: dict[str, list[str]] = field(default_factory=dict)
    services: dict[str, list[str]] = field(default_factory=dict)
    opening_times: dict[str, str] = field(default_factory=dict)
    published_at: str | None = None
    media: list[MediaReference] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    auxiliary: dict[str, Any] = field(default_factory=dict)
    fetched_at: float = 0.0
