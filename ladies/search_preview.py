from __future__ import annotations

import logging
from typing import Any

from flask import Flask, jsonify, request

from .core.services.search import parse_search_request
from .errors import ExternalServiceError, ValidationError
from .source import (
    build_filter_path,
    build_search_url,
    build_session,
    http_get,
    parse_search_cards,
    parse_search_config,
    resolve_location,
)

LOGGER = logging.getLogger("ladies_explorer.search_preview")


def fetch_search_preview_count(
    session,
    *,
    city_slug: str,
    city_display: str,
    radius_km: int,
    category: str = "",
    age_min: int = 18,
    age_max: int = 60,
    height_min: int = 140,
    height_max: int = 200,
    selected: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Fetch only page 1 and return a trustworthy exact-radius advert count."""
    filter_path = build_filter_path(
        category, age_min, age_max, height_min, height_max, selected
    )
    url = build_search_url(
        city_slug=city_slug,
        radius_km=radius_km,
        page=1,
        filter_path=filter_path,
        api=False,
    )
    response = http_get(
        session,
        url,
        context="search-preview-page-1",
        expected_statuses=(200, 404),
    )
    if response.status_code == 404:
        return {
            "count": 0,
            "city": city_display,
            "city_slug": city_slug,
            "radius_km": radius_km,
            "filter_path": filter_path,
            "count_source": "page-1-404",
        }

    response.raise_for_status()
    config = parse_search_config(response.text)
    distance = config.get("distance_searched")
    if distance is not None and int(distance) != int(radius_km):
        # The upstream site may automatically enlarge the radius for sparse filter
        # combinations. Never expose that widened count as if it matched the user's
        # explicit radius. Treat it as an exact-radius boundary with zero trusted
        # results instead of returning a validation error that leaves stale UI data.
        LOGGER.info(
            "Search preview hard-radius boundary requested_radius=%s server_radius=%s filter_path=%s",
            radius_km,
            distance,
            filter_path or "<none>",
        )
        return {
            "count": 0,
            "city": city_display,
            "city_slug": city_slug,
            "radius_km": radius_km,
            "filter_path": filter_path,
            "count_source": "radius-boundary-empty",
            "radius_boundary_stopped": True,
            "server_distance_seen": int(distance),
        }

    count = config.get("advert_count")
    source = "search-config"
    if count is None:
        count = len({profile.advert_id for profile in parse_search_cards(response.text)})
        source = "page-1-cards"

    return {
        "count": int(count or 0),
        "city": city_display,
        "city_slug": city_slug,
        "radius_km": radius_km,
        "filter_path": filter_path,
        "count_source": source,
    }


def install_search_preview(
    app: Flask,
    *,
    default_city: str = "leipzig",
    default_radius: int = 20,
) -> None:
    # Reuse the same HTTP session as the canonical SearchService when installed
    # through the normal application composition path.
    http = app.extensions.get("ladies_http") or build_session()

    @app.post("/api/search-preview")
    def api_search_preview():
        payload = request.get_json(silent=True) or {}
        try:
            params = parse_search_request(
                payload,
                default_city=default_city,
                default_radius=default_radius,
            )
            city_slug, city_display = resolve_location(http, params["city_raw"])
            result = fetch_search_preview_count(
                http,
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
            return jsonify(**result)
        except ValidationError:
            raise
        except (ValueError, RuntimeError) as exc:
            LOGGER.warning("Search preview rejected: %s", exc)
            raise ValidationError(str(exc)) from exc
        except Exception as exc:
            LOGGER.exception("Search preview failed")
            raise ExternalServiceError("Suchvorschau konnte nicht geladen werden.") from exc
