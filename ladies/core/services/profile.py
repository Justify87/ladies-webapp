from __future__ import annotations

import logging

from ladies_v9.domain import SearchProfile
from ladies_v9.errors import ConflictError, ExternalServiceError, NotFoundError, ValidationError
from ladies_v9.parser import ADVERT_ID_RE, parse_profile_html
from ladies_v9.source import http_get
from ladies_v9.utils import category_from_url, safe_profile_url

LOGGER = logging.getLogger("ladies_explorer.profile")


class ProfileService:
    def __init__(self, repository, http_session, limiter):
        self.repository = repository
        self.http = http_session
        self.limiter = limiter

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
            match = ADVERT_ID_RE.search(response.text)
            returned_id = match.group(1) if match else None
            if returned_id != advert_id:
                self.repository.update_listing_status(
                    advert_id,
                    status="UNKNOWN",
                    http_status=response.status_code,
                    details={"returned_id": returned_id, "final_url": response.url},
                )
                raise ConflictError("Die Profilantwort war nicht eindeutig.")
            profile = parse_profile_html(response.text, response.url, fallback)
            payload = self.repository.ingest_profile(
                external_listing_id=advert_id,
                profile=profile,
                raw_html=response.text,
                http_session=self.http,
            )
            LOGGER.info(
                "Profile load complete advert_id=%s person_id=%s media=%s",
                advert_id,
                payload.get("identity", {}).get("person_id"),
                len(payload.get("profile", {}).get("media", [])),
            )
            return payload
        except (ValidationError, NotFoundError, ConflictError):
            raise
        except Exception as exc:
            LOGGER.exception("Profile load failed advert_id=%s", advert_id)
            raise ExternalServiceError("Profil konnte nicht geladen werden.") from exc
