from __future__ import annotations

import logging
from typing import Any

from flask import Flask, jsonify, request

from .errors import ExternalServiceError, ValidationError
from .request_parsing import read_int, read_mapping
from .source import (
    build_filter_path,
    build_search_url,
    build_session,
    http_get,
    parse_search_cards,
    parse_search_config,
    resolve_location,
)
from .utils import clean_text

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
    """Fetch only page 1 and return the server-side advert count."""
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
        raise ValidationError(
            f"Suchvorschau meldet Radius {distance} km, angefordert waren {radius_km} km."
        )

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
    http = build_session()

    @app.post("/api/search-preview")
    def api_search_preview():
        payload = request.get_json(silent=True) or {}
        try:
            city_raw = clean_text(str(payload.get("city") or default_city))
            radius = read_int(payload, "radius", default=default_radius, minimum=1, maximum=200)
            category = clean_text(str(payload.get("category") or ""))
            age_min = read_int(payload, "age_min", default=18, minimum=18, maximum=60)
            age_max = read_int(payload, "age_max", default=60, minimum=age_min, maximum=60)
            height_min = read_int(payload, "height_min", default=140, minimum=140, maximum=200)
            height_max = read_int(payload, "height_max", default=200, minimum=height_min, maximum=200)
            selected = read_mapping(payload, "selected")
            city_slug, city_display = resolve_location(http, city_raw)
            result = fetch_search_preview_count(
                http,
                city_slug=city_slug,
                city_display=city_display,
                radius_km=radius,
                category=category,
                age_min=age_min,
                age_max=age_max,
                height_min=height_min,
                height_max=height_max,
                selected=selected,
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
