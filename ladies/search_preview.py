from __future__ import annotations

import logging
import re
from typing import Any

from flask import Flask, jsonify, request

from .core.services.search import parse_search_request
from .errors import ExternalServiceError, ValidationError
from .source import (
    build_filter_path,
    build_search_url,
    build_session,
    fetch_search_configured,
    http_get,
    parse_search_cards,
    parse_search_config,
    resolve_location,
)

LOGGER = logging.getLogger("ladies_explorer.search_preview")
_RADIUS_RE = re.compile(r"meldet Radius\s+(\d+)\s+km", re.IGNORECASE)


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
    """Fetch page 1 and report the count Ladies actually serves for the filter combination."""
    filter_path = build_filter_path(category, age_min, age_max, height_min, height_max, selected)
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
            "effective_radius_km": radius_km,
            "radius_auto_expanded": False,
            "filter_path": filter_path,
            "count_source": "page-1-404",
        }

    response.raise_for_status()
    config = parse_search_config(response.text)
    distance = config.get("distance_searched")
    effective_radius = int(distance) if distance is not None else int(radius_km)
    auto_expanded = effective_radius != int(radius_km)
    if auto_expanded:
        LOGGER.info(
            "Search preview upstream radius expansion requested_radius=%s effective_radius=%s filter_path=%s",
            radius_km,
            effective_radius,
            filter_path or "<none>",
        )

    count = config.get("advert_count")
    source = "search-config-auto-radius" if auto_expanded else "search-config"
    if count is None:
        count = len({profile.advert_id for profile in parse_search_cards(response.text)})
        source = "page-1-cards-auto-radius" if auto_expanded else "page-1-cards"

    return {
        "count": int(count or 0),
        "city": city_display,
        "city_slug": city_slug,
        "radius_km": int(radius_km),
        "effective_radius_km": effective_radius,
        "radius_auto_expanded": auto_expanded,
        "filter_path": filter_path,
        "count_source": source,
    }


def fetch_transient_search(
    session,
    *,
    params: dict[str, Any],
    city_slug: str,
    city_display: str,
) -> dict[str, Any]:
    """Fetch a full result set without creating a SearchRun or changing persisted search history."""
    requested_radius = int(params["radius"])
    effective_radius = requested_radius
    auto_expanded = False

    def fetch(radius: int):
        return fetch_search_configured(
            session=session,
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

    try:
        profiles, meta = fetch(requested_radius)
    except RuntimeError as exc:
        match = _RADIUS_RE.search(str(exc))
        if not match:
            raise
        effective_radius = int(match.group(1))
        if effective_radius < 1 or effective_radius > 200:
            raise
        auto_expanded = effective_radius != requested_radius
        profiles, meta = fetch(effective_radius)

    metadata = dict(meta or {})
    metadata["requested_radius_km"] = requested_radius
    metadata["effective_radius_km"] = effective_radius
    metadata["radius_auto_expanded"] = auto_expanded
    results = [
        {
            "advert_id": profile.advert_id,
            "name": profile.name,
            "city": profile.city,
            "category": profile.category,
            "profile_url": profile.profile_url,
            "thumbnail_url": profile.thumbnail_url,
            "has_video": profile.has_video,
            "is_premium": profile.is_premium,
            "has_ai_content": profile.has_ai_content,
            "markers": profile.markers,
        }
        for profile in profiles
    ]
    return {"results": results, "meta": metadata}


def install_search_preview(
    app: Flask,
    *,
    default_city: str = "leipzig",
    default_radius: int = 20,
) -> None:
    http = app.extensions.get("ladies_http") or build_session()

    @app.post("/api/search-preview")
    def api_search_preview():
        payload = request.get_json(silent=True) or {}
        try:
            params = parse_search_request(payload, default_city=default_city, default_radius=default_radius)
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

    @app.post("/api/search-transient")
    def api_search_transient():
        payload = request.get_json(silent=True) or {}
        try:
            params = parse_search_request(payload, default_city=default_city, default_radius=default_radius)
            city_slug, city_display = resolve_location(http, params["city_raw"])
            return jsonify(**fetch_transient_search(
                http,
                params=params,
                city_slug=city_slug,
                city_display=city_display,
            ))
        except ValidationError:
            raise
        except (ValueError, RuntimeError) as exc:
            LOGGER.warning("Transient search rejected: %s", exc)
            raise ValidationError(str(exc)) from exc
        except Exception as exc:
            LOGGER.exception("Transient search failed")
            raise ExternalServiceError("Suche konnte nicht geladen werden.") from exc
