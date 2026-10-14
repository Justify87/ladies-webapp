from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Any
from urllib.parse import urlencode

import requests

from .domain import SearchProfile
from .parser import parse_search_cards, parse_search_config
from .search_catalog import (
    FILTER_GROUP_MAP,
    FILTER_GROUP_ORDER,
    canonical_category,
    filter_value_slug,
)
from .utils import BASE, clean_text, slugify_location_input

LOGGER = logging.getLogger("ladies_explorer")
DEFAULT_CITY = "leipzig"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
)


class FixedRateLimiter:
    def __init__(self, min_interval_seconds: float = 2.0):
        self.min_interval = max(1.0, float(min_interval_seconds))
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> float:
        with self._lock:
            now = time.monotonic()
            remaining = self.min_interval - (now - self._last)
            if remaining > 0:
                time.sleep(remaining)
            self._last = time.monotonic()
            return max(0.0, remaining)


class BatchRateLimiter:
    def __init__(self, batch_size: int = 3, pause_seconds: float = 0.8):
        self.batch_size = max(1, int(batch_size))
        self.pause_seconds = max(0.1, float(pause_seconds))
        self._used = 0
        self._next_batch_at = 0.0
        self._lock = threading.Lock()

    def wait(self) -> float:
        with self._lock:
            now = time.monotonic()
            waited = 0.0
            if self._used >= self.batch_size:
                remaining = self._next_batch_at - now
                if remaining > 0:
                    time.sleep(remaining)
                    waited = remaining
                self._used = 0
                now = time.monotonic()
            elif self._used == 0 and now < self._next_batch_at:
                remaining = self._next_batch_at - now
                time.sleep(remaining)
                waited = remaining
                now = time.monotonic()
            self._used += 1
            if self._used >= self.batch_size:
                self._next_batch_at = now + self.pause_seconds
            return waited


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
        "DNT": "1",
    })
    return session


def http_get(
    session: requests.Session,
    url: str,
    *,
    context: str,
    timeout: float = 30,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    expected_statuses: tuple[int, ...] = (200,),
) -> requests.Response:
    started = time.monotonic()
    try:
        response = session.get(url, headers=headers, params=params, timeout=timeout)
    except requests.RequestException:
        LOGGER.exception("HTTP failed context=%s url=%s", context, url)
        raise
    elapsed = (time.monotonic() - started) * 1000
    level = logging.DEBUG if response.status_code in expected_statuses else logging.WARNING
    LOGGER.log(
        level,
        "HTTP context=%s status=%s elapsed_ms=%.0f bytes=%s url=%s",
        context,
        response.status_code,
        elapsed,
        len(response.content or b""),
        response.url,
    )
    return response


def build_search_url(
    *,
    city_slug: str,
    radius_km: int,
    page: int,
    filter_path: str = "",
    api: bool = False,
) -> str:
    path = f"/{city_slug}/sex-anzeigen"
    if filter_path:
        path += f"/{filter_path.strip('/')}"
    if api:
        path = "/api" + path
    query = urlencode((("umkreis", radius_km), ("page", page)))
    return f"{BASE}{path}?{query}"


def resolve_location(session: requests.Session, raw: str) -> tuple[str, str]:
    """Resolve a user-entered city/region/address through the site's location API."""
    raw = clean_text(raw)
    if not raw:
        return DEFAULT_CITY, "Leipzig"
    try:
        response = http_get(
            session,
            f"{BASE}/api/location",
            context="location-resolve",
            params={"searchString": raw, "searchAddAlleRegionen": "false"},
            headers={"Accept": "application/json,*/*", "Referer": f"{BASE}/"},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()

        candidates: list[dict[str, Any]] = []

        def walk(obj: Any):
            if isinstance(obj, dict):
                candidates.append(obj)
                for value in obj.values():
                    walk(value)
            elif isinstance(obj, list):
                for value in obj:
                    walk(value)

        walk(data)

        lowered = raw.lower()
        scored: list[tuple[int, dict[str, Any]]] = []
        for item in candidates:
            keys = {str(key).lower(): value for key, value in item.items()}
            slug = keys.get("slug") or keys.get("seoslug") or keys.get("urlslug")
            name = keys.get("name") or keys.get("title") or keys.get("ort") or keys.get("locationname")
            if not slug or not name:
                continue
            name_s = clean_text(str(name))
            score = 0
            if name_s.lower() == lowered:
                score += 100
            elif name_s.lower().startswith(lowered):
                score += 70
            elif lowered in name_s.lower():
                score += 40
            scored.append((score, item))

        if scored:
            _, item = sorted(scored, key=lambda value: value[0], reverse=True)[0]
            keys = {str(key).lower(): value for key, value in item.items()}
            slug = clean_text(str(keys.get("slug") or keys.get("seoslug") or keys.get("urlslug")))
            name = clean_text(str(keys.get("name") or keys.get("title") or keys.get("ort") or keys.get("locationname")))
            if slug:
                return slug, name or raw
    except Exception:
        LOGGER.exception("Location resolution failed input=%r; using conservative slug fallback", raw)

    fallback = slugify_location_input(raw)
    LOGGER.warning("Location resolution produced no exact candidate input=%r fallback_slug=%s", raw, fallback)
    return fallback, raw


def build_filter_path(
    category: str,
    age_min: int,
    age_max: int,
    height_min: int,
    height_max: int,
    selected: dict[str, list[str]] | None,
) -> str:
    groups: dict[str, list[str]] = {}

    if age_min > 18 or age_max < 60:
        groups["alter"] = [str(age_min), str(age_max)]

    if height_min > 140 or height_max < 200:
        groups["groesse"] = [str(height_min), str(height_max)]

    for raw_group, raw_values in (selected or {}).items():
        if not raw_values:
            continue
        group = FILTER_GROUP_MAP.get(raw_group)
        if not group:
            raise ValueError(f"Unbekannte Filtergruppe: {raw_group}")
        slugs: list[str] = []
        for value in raw_values:
            slug = filter_value_slug(raw_group, value)
            if slug not in slugs:
                slugs.append(slug)
        if slugs:
            groups.setdefault(group, [])
            for slug in slugs:
                if slug not in groups[group]:
                    groups[group].append(slug)

    filter_segment = ";".join(
        f"{group}:{','.join(groups[group])}"
        for group in FILTER_GROUP_ORDER
        if group in groups and groups[group]
    )

    category_slug = canonical_category(category)
    parts = [part for part in (category_slug, filter_segment) if part]
    return "/".join(parts)


def fetch_search_configured(
    session: requests.Session,
    city_slug: str,
    city_display: str,
    radius_km: int,
    category: str = "",
    age_min: int = 18,
    age_max: int = 60,
    height_min: int = 140,
    height_max: int = 200,
    selected: dict[str, list[str]] | None = None,
    page_delay: float = 0.35,
) -> tuple[list[SearchProfile], dict[str, Any]]:
    filter_path = build_filter_path(
        category, age_min, age_max, height_min, height_max, selected
    )
    first_url = build_search_url(
        city_slug=city_slug,
        radius_km=radius_km,
        page=1,
        filter_path=filter_path,
        api=False,
    )
    LOGGER.info(
        "Search start city=%s radius=%s filter_path=%s url=%s",
        city_slug,
        radius_km,
        filter_path or "<none>",
        first_url,
    )
    first_response = http_get(
        session,
        first_url,
        context="search-page-1",
        expected_statuses=(200, 404),
    )
    if first_response.status_code == 404:
        LOGGER.info(
            "Search returned no page for exact filter combination; treating as empty result url=%s",
            first_url,
        )
        return [], {
            "city": city_slug,
            "city_display": city_display,
            "radius_km": radius_km,
            "filter_path": filter_path,
            "first_url": first_url,
            "total_pages": 0,
            "server_advert_count": 0,
            "distance_searched": str(radius_km),
            "pagination_source": "page-1-404-empty",
            "radius_boundary_stopped": False,
            "unique_count": 0,
        }
    first_response.raise_for_status()
    first_html = first_response.text
    profiles = parse_search_cards(first_html)
    first_config = parse_search_config(first_html)

    first_distance = first_config.get("distance_searched")
    if first_distance is not None and first_distance != radius_km:
        raise RuntimeError(
            f"Fail-closed: Bereits Seite 1 meldet Radius {first_distance} km, "
            f"angefordert waren {radius_km} km."
        )

    metadata_available = "total_pages" in first_config
    total_pages = max(1, first_config.get("total_pages", 1))
    server_count = first_config.get("advert_count")

    meta: dict[str, Any] = {
        "city": city_slug,
        "city_display": city_display,
        "radius_km": radius_km,
        "filter_path": filter_path,
        "first_url": first_url,
        "total_pages": total_pages,
        "server_advert_count": server_count,
        "distance_searched": str(first_distance if first_distance is not None else radius_km),
        "pagination_source": "page-1-search-config" if metadata_available else "api-fallback",
        "radius_boundary_stopped": False,
    }

    if metadata_available:
        LOGGER.debug(
            "Search page-1 metadata total_pages=%s current_page=%s advert_count=%s "
            "distance_searched=%s parsed_cards=%s",
            total_pages,
            first_config.get("current_page"),
            server_count,
            first_distance,
            len(profiles),
        )
    else:
        LOGGER.warning(
            "SearchConfig pagination metadata missing on page 1; using guarded API fallback url=%s",
            first_url,
        )
        # Compatibility fallback for a future site markup change. The response is
        # inspected before any cards are accepted; a widened radius ends pagination
        # rather than turning the whole search into an error.
        time.sleep(max(0.0, page_delay))
        second_url = build_search_url(
            city_slug=city_slug,
            radius_km=radius_km,
            page=2,
            filter_path=filter_path,
            api=True,
        )
        response = http_get(
            session,
            second_url,
            context="search-api-page-2-fallback",
            headers={"Accept": "*/*", "Referer": f"{BASE}/"},
            expected_statuses=(200, 404),
        )
        if response.status_code == 404:
            total_pages = 1
        else:
            response.raise_for_status()
            payload = response.json()
            for flag in ("snapshotUpdatedDueToNewDay", "snapshotUpdatedDueToNewIndex"):
                if flag in payload:
                    meta[flag] = payload.get(flag)
            distance = str(payload.get("distanceSearched", ""))
            if distance and distance != str(radius_km):
                LOGGER.info(
                    "Hard-radius boundary reached during fallback page=2 requested_radius=%s "
                    "server_radius=%s; keeping page-1 results only",
                    radius_km,
                    distance,
                )
                total_pages = 1
                meta["radius_boundary_stopped"] = True
                meta["server_distance_seen"] = distance
            else:
                pagination = payload.get("pagination") or {}
                total_pages = max(1, int(pagination.get("totalPages") or 1))
                meta["server_advert_count"] = payload.get("advertCount")
                if total_pages >= 2:
                    profiles.extend(parse_search_cards(payload.get("content") or ""))
        meta["total_pages"] = total_pages

    # Page 1 is already in the HTML. Only request pages that page 1 itself says
    # belong to the exact-radius result set. This is the key hard-radius guard.
    first_api_page = 2 if metadata_available else 3
    for page in range(first_api_page, total_pages + 1):
        time.sleep(max(0.0, page_delay))
        url = build_search_url(
            city_slug=city_slug,
            radius_km=radius_km,
            page=page,
            filter_path=filter_path,
            api=True,
        )
        response = http_get(
            session,
            url,
            context=f"search-api-page-{page}",
            headers={"Accept": "*/*", "Referer": f"{BASE}/"},
            expected_statuses=(200, 404),
        )
        if response.status_code == 404:
            LOGGER.warning(
                "Search pagination ended early with 404 page=%s expected_total_pages=%s url=%s",
                page,
                total_pages,
                url,
            )
            meta["pagination_ended_early_at"] = page
            break

        response.raise_for_status()
        payload = response.json()
        for flag in ("snapshotUpdatedDueToNewDay", "snapshotUpdatedDueToNewIndex"):
            if flag in payload:
                meta[flag] = payload.get(flag)
        distance = str(payload.get("distanceSearched", ""))
        if distance and distance != str(radius_km):
            # Never accept cards from an automatically enlarged radius. Keep the
            # exact-radius pages already collected instead of returning HTTP 502.
            LOGGER.warning(
                "Hard-radius boundary reached page=%s requested_radius=%s server_radius=%s; "
                "discarding this page and stopping pagination",
                page,
                radius_km,
                distance,
            )
            meta["radius_boundary_stopped"] = True
            meta["radius_boundary_page"] = page
            meta["server_distance_seen"] = distance
            break

        profiles.extend(parse_search_cards(payload.get("content") or ""))

    unique: dict[str, SearchProfile] = {}
    for profile in profiles:
        unique.setdefault(profile.advert_id, profile)

    result = list(unique.values())
    meta["unique_count"] = len(result)
    if meta.get("server_advert_count") is not None and len(result) != meta["server_advert_count"]:
        LOGGER.warning(
            "Search count mismatch exact-radius parsed_unique=%s server_advert_count=%s "
            "total_pages=%s boundary_stopped=%s",
            len(result),
            meta["server_advert_count"],
            meta["total_pages"],
            meta.get("radius_boundary_stopped"),
        )
    LOGGER.info(
        "Search complete city=%s radius=%s filter_path=%s pages=%s unique=%s "
        "server_count=%s pagination_source=%s boundary_stopped=%s",
        city_slug,
        radius_km,
        filter_path or "<none>",
        meta["total_pages"],
        len(result),
        meta.get("server_advert_count"),
        meta.get("pagination_source"),
        meta.get("radius_boundary_stopped"),
    )
    return result, meta
