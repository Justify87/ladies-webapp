from __future__ import annotations

import logging
import threading
import time

from ladies_v9.business_profiles import business_website_url
from ladies_v9.domain import SearchProfile
from ladies_v9.errors import ConflictError, ExternalServiceError, NotFoundError, ValidationError
from ladies_v9.parser import ADVERT_ID_RE, parse_profile_html
from ladies_v9.source import http_get
from ladies_v9.utils import category_from_url, safe_profile_url

LOGGER = logging.getLogger("ladies_explorer.profile")
PROFILE_SOURCE_CACHE_SECONDS = 120.0


class ProfileService:
    def __init__(self, repository, http_session, limiter, classification_repository=None):
        self.repository = repository
        self.classification_repository = classification_repository
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
            expired = [
                key for key, (cached_at, _html, _url) in self._source_cache.items()
                if now - cached_at > PROFILE_SOURCE_CACHE_SECONDS
            ]
            for key in expired:
                self._source_cache.pop(key, None)
                self._profile_locks.pop(key, None)

    def _source_for_advert(
        self,
        advert_id: str,
        *,
        target: dict,
        profile_url: str,
        context: str,
    ) -> tuple[str, str]:
        cached = self._cached_source(advert_id)
        if cached is not None:
            LOGGER.debug("Profile source cache hit advert_id=%s", advert_id)
            return cached
        self.limiter.wait()
        response = http_get(
            self.http,
            profile_url,
            context=context,
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
        return response_text, response_url

    def _classification_from_html(self, advert_id: str, raw_html: str) -> dict:
        website = business_website_url(raw_html)
        payload = {
            "business_profile_known": True,
            "is_business_profile": bool(website),
            "business_website_url": website,
            "business_profile_checked_at": None,
        }
        if self.classification_repository is not None:
            payload = self.classification_repository.save_business_profile_classification(
                advert_id,
                is_business_profile=bool(website),
                business_website_url=website,
                http_status=200,
            )
        return payload

    def classify_business_profile(self, advert_id: str) -> dict:
        advert_id = str(advert_id or "")
        if not advert_id.isdigit():
            raise ValidationError("Ungültige Anzeigen-ID")
        if self.classification_repository is not None:
            known = self.classification_repository.business_profile_classification(advert_id)
            if known and known.get("business_profile_known"):
                return known
        target = self.repository.listing_target(advert_id)
        if not target:
            raise NotFoundError("Anzeige ist lokal nicht bekannt. Zuerst über die Suche laden.")
        profile_url = safe_profile_url(target.get("current_url") or "")
        if not profile_url:
            raise NotFoundError("Keine sichere Ladies-Profil-URL gespeichert.")
        try:
            with self._profile_lock(advert_id):
                if self.classification_repository is not None:
                    known = self.classification_repository.business_profile_classification(advert_id)
                    if known and known.get("business_profile_known"):
                        return known
                response_text, _response_url = self._source_for_advert(
                    advert_id,
                    target=target,
                    profile_url=profile_url,
                    context=f"business-profile-{advert_id}",
                )
                result = self._classification_from_html(advert_id, response_text)
                LOGGER.info(
                    "Business profile classified advert_id=%s business_profile=%s",
                    advert_id,
                    bool(result.get("is_business_profile")),
                )
                return result
        except (ValidationError, NotFoundError, ConflictError):
            raise
        except Exception as exc:
            LOGGER.exception("Business profile classification failed advert_id=%s", advert_id)
            raise ExternalServiceError("Betriebsprofil konnte nicht geprüft werden.") from exc

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
            # advert and reuse the same short-lived upstream HTML for both full
            # profile ingestion and lightweight business-profile classification.
            with self._profile_lock(advert_id):
                LOGGER.info("Profile load start advert_id=%s person_id=%s", advert_id, target.get("person_id"))
                response_text, response_url = self._source_for_advert(
                    advert_id,
                    target=target,
                    profile_url=profile_url,
                    context=f"profile-{advert_id}",
                )
                classification = self._classification_from_html(advert_id, response_text)
                profile = parse_profile_html(response_text, response_url, fallback)
                payload = self.repository.ingest_profile(
                    external_listing_id=advert_id,
                    profile=profile,
                    raw_html=response_text,
                    http_session=self.http,
                )
                profile_payload = payload.get("profile")
                if isinstance(profile_payload, dict):
                    profile_payload.update(classification)
                LOGGER.info(
                    "Profile load complete advert_id=%s person_id=%s media=%s business_profile=%s",
                    advert_id,
                    payload.get("identity", {}).get("person_id"),
                    len(payload.get("profile", {}).get("media", [])),
                    bool(classification.get("is_business_profile")),
                )
                return payload
        except (ValidationError, NotFoundError, ConflictError):
            raise
        except Exception as exc:
            LOGGER.exception("Profile load failed advert_id=%s", advert_id)
            raise ExternalServiceError("Profil konnte nicht geladen werden.") from exc
