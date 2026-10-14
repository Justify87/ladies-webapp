from __future__ import annotations

import logging
import threading
import time

from bs4 import BeautifulSoup

from ladies_v9.domain import SearchProfile
from ladies_v9.errors import ConflictError, ExternalServiceError, NotFoundError, ValidationError
from ladies_v9.parser import ADVERT_ID_RE, parse_profile_html
from ladies_v9.source import http_get
from ladies_v9.utils import category_from_url, clean_text, safe_profile_url

LOGGER = logging.getLogger("ladies_explorer.profile")
PROFILE_SOURCE_CACHE_SECONDS = 120.0


def _business_website_url(raw_html: str) -> str | None:
    """Return the advertiser-supplied website marked by Ladies on profile pages.

    Ladies uses ``a.einzelansicht-url[href]`` for the website attached to the
    advert itself.  Other external links on the page (social, ads, navigation)
    deliberately do not count as business-profile evidence.
    """
    soup = BeautifulSoup(raw_html, "html.parser")
    link = soup.select_one("a.einzelansicht-url[href]")
    href = clean_text(link.get("href")) if link else ""
    return href or None


class ProfileService:
    def __init__(self, repository, http_session, limiter):
        self.repository = repository
        self.http = http_session
        self.limiter = limiter
        self._cache_lock = threading.Lock()
        self._profile_locks: dict[str, threading.Lock] = {}
        self._source_cache: dict[str, tuple[float, str, str]] = {}

    def _profile_lock(self, advert_id: str) -> threading.Lock:
        with self._cache_lock:
            return self._profile_locks.setdefault(advert_id, threading.Lock())

    def _cached_source(self, advert_id: str) -> tuple[str, str] | None:
        with self._cache_lock:
            cached = self._source_cache.get(advert_id)
            if not cached:
                return None
            cached_at, html, url = cached
            if time.monotonic() - cached_at > PROFILE_SOURCE_CACHE_SECONDS:
                self._source_cache.pop(advert_id, None)
                return None
            return html, url

    def _remember_source(self, advert_id: str, html: str, url: str) -> None:
        with self._cache_lock:
            now = time.monotonic()
            self._source_cache[advert_id] = (now, html, url)
            # Keep the short-lived cache bounded during very large browse sessions.
            expired = [
                key for key, (cached_at, _html, _url) in self._source_cache.items()
                if now - cached_at > PROFILE_SOURCE_CACHE_SECONDS
            ]
            for key in expired:
                self._source_cache.pop(key, None)
                self._profile_locks.pop(key, None)

    def load(self, advert_id: str) -> dict:
        advert_id = str(advert_id or "")
        if not advert_id.isdigit():
            raise ValidationError("Ungültige Anzeigen-ID")
        target = self.repository.listing_target(advert_id)
        if not target:
            raise NotFoundError("Anzeige ist lokal nicht bekannt. Zuerst über die Suche laden.")
        profile_url = safe_profile_url(target.get("current_url") or "")
        if not profile_url:
            raise NotFoundError("Keine sichere Ladies-Profil-URL gespeichert.")
        fallback = SearchProfile(
            advert_id=advert_id,
            name="",
            city="",
            category=target.get("category") or category_from_url(profile_url),
            profile_url=profile_url,
            thumbnail_url=None,
        )
        try:
            # A profile can be requested by swipe prefetch, grid rendering and a
            # user opening the detail view almost simultaneously. Serialize per
            # advert and reuse only the upstream HTML briefly; ingestion still
            # runs on every request so local user/identity state stays current.
            with self._profile_lock(advert_id):
                cached = self._cached_source(advert_id)
                if cached is not None:
                    response_text, response_url = cached
                    LOGGER.debug("Profile source cache hit advert_id=%s", advert_id)
                else:
                    LOGGER.info("Profile load start advert_id=%s person_id=%s", advert_id, target.get("person_id"))
                    self.limiter.wait()
                    response = http_get(
                        self.http,
                        profile_url,
                        context=f"profile-{advert_id}",
                        expected_statuses=(200, 404, 410),
                    )
                    if response.status_code in {404, 410}:
                        self.repository.update_listing_status(
                            advert_id,
                            status="UNAVAILABLE",
                            http_status=response.status_code,
                        )
                        raise NotFoundError("Anzeige ist nicht mehr verfügbar.")
                    response.raise_for_status()
                    response_text = response.text
                    response_url = response.url
                    match = ADVERT_ID_RE.search(response_text)
                    returned_id = match.group(1) if match else None
                    if returned_id != advert_id:
                        self.repository.update_listing_status(
                            advert_id,
                            status="UNKNOWN",
                            http_status=response.status_code,
                            details={"returned_id": returned_id, "final_url": response_url},
                        )
                        raise ConflictError("Die Profilantwort war nicht eindeutig.")
                    self._remember_source(advert_id, response_text, response_url)

                profile = parse_profile_html(response_text, response_url, fallback)
                business_website_url = _business_website_url(response_text)
                if business_website_url:
                    profile.auxiliary["business_website_url"] = business_website_url
                    profile.provenance["business_website_url"] = {
                        "source": "DOM_VISIBLE",
                        "rank": 100,
                    }
                payload = self.repository.ingest_profile(
                    external_listing_id=advert_id,
                    profile=profile,
                    raw_html=response_text,
                    http_session=self.http,
                )
                # The repository can legitimately reuse an older identical
                # snapshot whose auxiliary metadata predates this classifier.
                # Always attach the current HTML-derived classification to the
                # response so filtering is correct immediately, without a DB
                # migration or waiting for another profile change.
                profile_payload = payload.get("profile")
                if isinstance(profile_payload, dict):
                    profile_payload["is_business_profile"] = bool(business_website_url)
                    profile_payload["business_website_url"] = business_website_url
                LOGGER.info(
                    "Profile load complete advert_id=%s person_id=%s media=%s business_profile=%s",
                    advert_id,
                    payload.get("identity", {}).get("person_id"),
                    len(payload.get("profile", {}).get("media", [])),
                    bool(business_website_url),
                )
                return payload
        except (ValidationError, NotFoundError, ConflictError):
            raise
        except Exception as exc:
            LOGGER.exception("Profile load failed advert_id=%s", advert_id)
            raise ExternalServiceError("Profil konnte nicht geladen werden.") from exc
