from __future__ import annotations

import logging
import re
from typing import Any

from ladies_v9.errors import ExternalServiceError, ValidationError
from ladies_v9.request_parsing import read_int, read_mapping
from ladies_v9.source import build_filter_path, fetch_search_configured, resolve_location
from ladies_v9.utils import clean_text

LOGGER = logging.getLogger("ladies_explorer.search")
_RADIUS_RE = re.compile(r"meldet Radius\s+(\d+)\s+km", re.IGNORECASE)
ALLOWED_RADII = (5, 10, 20, 30, 50, 70, 100)


def parse_search_request(
    payload: dict[str, Any],
    *,
    default_city: str,
    default_radius: int,
) -> dict[str, Any]:
    city_raw = clean_text(str(payload.get("city") or default_city))
    radius = read_int(payload, "radius", default=default_radius, minimum=1, maximum=200)
    if radius not in ALLOWED_RADII:
        raise ValidationError(f"Ungültiger Umkreis. Erlaubt sind: {', '.join(str(x) for x in ALLOWED_RADII)} km.")
    category = clean_text(str(payload.get("category") or ""))
    age_min = read_int(payload, "age_min", default=18, minimum=18, maximum=60)
    age_max = read_int(payload, "age_max", default=60, minimum=age_min, maximum=60)
    height_min = read_int(payload, "height_min", default=140, minimum=140, maximum=200)
    height_max = read_int(payload, "height_max", default=200, minimum=height_min, maximum=200)
    selected = read_mapping(payload, "selected")
    if not category and selected.get("treff"):
        category = "huren-hostessen"
    return {
        "city_raw": city_raw,
        "radius": radius,
        "category": category,
        "age_min": age_min,
        "age_max": age_max,
        "height_min": height_min,
        "height_max": height_max,
        "selected": selected,
    }


class SearchService:
    """Canonical search orchestration for persisted and transient result sets."""

    def __init__(self, repository, http_session, *, default_city: str, default_radius: int):
        self.repository = repository
        self.http = http_session
        self.default_city = default_city
        self.default_radius = int(default_radius)

    def _fetch(self, params: dict[str, Any], city_slug: str, city_display: str, radius: int):
        return fetch_search_configured(
            session=self.http,
            city_slug=city_slug,
            city_display=city_display,
            radius_km=radius,
            category=params["category"],
            age_min=params["age_min"],
            age_max=params["age_max"],
            height_min=params["height_min"],
            height_max=params["height_max"],
            selected=params["selected"],
        )

    def _prepare(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
        params = parse_search_request(payload, default_city=self.default_city, default_radius=self.default_radius)
        LOGGER.info(
            "Search start city=%s radius=%s category=%s age=%s-%s filters=%s",
            params["city_raw"], params["radius"], params["category"] or "ALL",
            params["age_min"], params["age_max"], sorted(params["selected"]),
        )
        city_slug, city_display = resolve_location(self.http, params["city_raw"])
        return params, city_slug, city_display

    def _fetch_with_radius(
        self,
        params: dict[str, Any],
        city_slug: str,
        city_display: str,
    ):
        requested_radius = int(params["radius"])
        effective_radius = requested_radius
        auto_expanded = False
        try:
            profiles, meta = self._fetch(params, city_slug, city_display, requested_radius)
        except RuntimeError as exc:
            match = _RADIUS_RE.search(str(exc))
            if not match:
                raise
            effective_radius = int(match.group(1))
            if effective_radius < 1 or effective_radius > 200:
                raise
            auto_expanded = effective_radius != requested_radius
            LOGGER.info(
                "Search follows upstream radius expansion requested_radius=%s effective_radius=%s filter_path=%s",
                requested_radius,
                effective_radius,
                build_filter_path(
                    params["category"], params["age_min"], params["age_max"],
                    params["height_min"], params["height_max"], params["selected"],
                ) or "<none>",
            )
            profiles, meta = self._fetch(params, city_slug, city_display, effective_radius)

        metadata = dict(meta or {})
        metadata["requested_radius_km"] = requested_radius
        metadata["effective_radius_km"] = effective_radius
        metadata["radius_auto_expanded"] = auto_expanded
        return profiles, metadata

    def _execute(self, payload: dict[str, Any], *, persist_history: bool) -> dict[str, Any]:
        params, city_slug, city_display = self._prepare(payload)
        run_id = None
        if persist_history:
            run_id = self.repository.create_search_run(
                city=city_display,
                city_slug=city_slug,
                radius_km=params["radius"],
                filters={
                    "category": params["category"],
                    "age_min": params["age_min"],
                    "age_max": params["age_max"],
                    "height_min": params["height_min"],
                    "height_max": params["height_max"],
                    "selected": params["selected"],
                },
            )

        profiles, meta = self._fetch_with_radius(params, city_slug, city_display)
        results = (
            self.repository.finish_search_run(run_id, profiles, meta)
            if run_id is not None
            else self.repository.sync_search_profiles(profiles)
        )
        LOGGER.info(
            "Search complete persisted=%s run_id=%s results=%s city=%s requested_radius=%s effective_radius=%s",
            persist_history, run_id, len(results), city_display,
            meta["requested_radius_km"], meta["effective_radius_km"],
        )
        response = {"results": results, "meta": meta}
        if run_id is not None:
            response["search_run_id"] = run_id
        return response

    def _guarded_execute(self, payload: dict[str, Any], *, persist_history: bool) -> dict[str, Any]:
        try:
            return self._execute(payload, persist_history=persist_history)
        except ValidationError:
            raise
        except (ValueError, RuntimeError) as exc:
            LOGGER.warning("Search rejected: %s", exc)
            raise ValidationError(str(exc)) from exc
        except Exception as exc:
            LOGGER.exception("Search failed persisted=%s", persist_history)
            raise ExternalServiceError("Suche konnte nicht geladen werden.") from exc

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._guarded_execute(payload, persist_history=True)

    def execute_transient(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return canonical local result rows without creating SearchRun history."""
        return self._guarded_execute(payload, persist_history=False)
