from __future__ import annotations

import logging
from typing import Any

from ladies_v9.errors import ExternalServiceError, ValidationError
from ladies_v9.request_parsing import read_int, read_mapping
from ladies_v9.source import fetch_search_configured, resolve_location
from ladies_v9.utils import clean_text

LOGGER = logging.getLogger("ladies_explorer.search")


def parse_search_request(
    payload: dict[str, Any],
    *,
    default_city: str,
    default_radius: int,
) -> dict[str, Any]:
    """Normalize the shared search/preview request contract in one place."""
    city_raw = clean_text(str(payload.get("city") or default_city))
    radius = read_int(payload, "radius", default=default_radius, minimum=1, maximum=200)
    category = clean_text(str(payload.get("category") or ""))
    age_min = read_int(payload, "age_min", default=18, minimum=18, maximum=60)
    age_max = read_int(payload, "age_max", default=60, minimum=age_min, maximum=60)
    height_min = read_int(payload, "height_min", default=140, minimum=140, maximum=200)
    height_max = read_int(payload, "height_max", default=200, minimum=height_min, maximum=200)
    selected = read_mapping(payload, "selected")

    # Ladies exposes the Treffpunkt facets below the canonical Ladies category.
    # Sending them against the unscoped /sex-anzeigen route makes the upstream
    # site silently widen the radius (observed as 50/70 km for a 20 km request),
    # which our fail-closed radius guard correctly rejects. Keep explicit user
    # categories untouched, but scope an otherwise category-less Treffpunkt
    # search to the canonical Ladies category for both preview and real search.
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
    def __init__(self, repository, http_session, *, default_city: str, default_radius: int):
        self.repository = repository
        self.http = http_session
        self.default_city = default_city
        self.default_radius = int(default_radius)

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            params = parse_search_request(
                payload,
                default_city=self.default_city,
                default_radius=self.default_radius,
            )
            LOGGER.info(
                "Search start city=%s radius=%s category=%s age=%s-%s filters=%s",
                params["city_raw"],
                params["radius"],
                params["category"] or "ALL",
                params["age_min"],
                params["age_max"],
                sorted(params["selected"]),
            )
            city_slug, city_display = resolve_location(self.http, params["city_raw"])
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
            profiles, meta = fetch_search_configured(
                session=self.http,
                city_slug=city_slug,
                city_display=city_display,
                radius_km=params["radius"],
                category=params["category"],
                age_min=params["age_min"],
                age_max=params["age_max"],
                height_min=params["height_min"],
                height_max=params["height_max"],
                selected=params["selected"],
            )
            results = self.repository.finish_search_run(run_id, profiles, meta)
            LOGGER.info(
                "Search complete run_id=%s results=%s city=%s radius=%s",
                run_id,
                len(results),
                city_display,
                params["radius"],
            )
            return {"search_run_id": run_id, "results": results, "meta": meta}
        except ValidationError:
            raise
        except (ValueError, RuntimeError) as exc:
            LOGGER.warning("Search rejected: %s", exc)
            raise ValidationError(str(exc)) from exc
        except Exception as exc:
            LOGGER.exception("Search failed")
            raise ExternalServiceError("Suche konnte nicht geladen werden.") from exc
