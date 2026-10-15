from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import re
from threading import Lock
from typing import Any

import orjson
import requests

from ladies_v9.source import FixedRateLimiter


LOGGER = logging.getLogger("ladies_explorer.map")
POSTAL_RE = re.compile(r"(?<!\d)(\d{5})(?!\d)")
ZIPPOPOTAM_URL = "https://api.zippopotam.us/DE/{postal_code}"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_city(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _postal_code(item: dict[str, Any]) -> str | None:
    for value in (item.get("postal_code"), item.get("hidden_location")):
        match = POSTAL_RE.search(str(value or ""))
        if match:
            return match.group(1)
    return None


class MapService:
    """Resolve search results to privacy-conscious map points.

    Exact hidden addresses are never sent to a third-party geocoder. If a stored
    location contains a postal code, only that five-digit code is resolved. Rows
    without a postal code fall back to the search area's city centroid.
    """

    def __init__(self, repository, *, data_dir: str | Path):
        self.repository = repository
        self.cache_path = Path(data_dir) / "map-geocode-cache.json"
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._cache = self._read_cache()
        self._http = requests.Session()
        self._http.headers.update({
            "User-Agent": "LadiesExplorer map-location-resolver",
            "Accept": "application/json",
        })
        self._city_limiter = FixedRateLimiter(1.1)

    def _read_cache(self) -> dict[str, dict[str, Any]]:
        try:
            value = orjson.loads(self.cache_path.read_bytes())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError, orjson.JSONDecodeError):
            return {}

    def _save_cache(self) -> None:
        with self._lock:
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_bytes(orjson.dumps(self._cache, option=orjson.OPT_SORT_KEYS))
            tmp.replace(self.cache_path)

    def _cached(self, key: str) -> dict[str, Any] | None:
        value = self._cache.get(key)
        if not isinstance(value, dict):
            return None
        try:
            return {
                **value,
                "lat": float(value["lat"]),
                "lng": float(value["lng"]),
            }
        except (KeyError, TypeError, ValueError):
            return None

    def _remember(self, key: str, *, lat: float, lng: float, precision: str, source: str) -> dict[str, Any]:
        value = {
            "lat": float(lat),
            "lng": float(lng),
            "precision": str(precision),
            "source": str(source),
            "updated_at": _now_iso(),
        }
        self._cache[key] = value
        try:
            self._save_cache()
        except OSError:
            LOGGER.warning("Map geocode cache could not be written path=%s", self.cache_path)
        return value

    def _resolve_postal(self, postal_code: str) -> dict[str, Any] | None:
        key = f"postal:DE:{postal_code}"
        cached = self._cached(key)
        if cached:
            return cached
        try:
            response = self._http.get(ZIPPOPOTAM_URL.format(postal_code=postal_code), timeout=6)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
            coordinates = []
            for place in data.get("places") or []:
                try:
                    coordinates.append((float(place["latitude"]), float(place["longitude"])))
                except (KeyError, TypeError, ValueError):
                    continue
            if not coordinates:
                return None
            lat = sum(item[0] for item in coordinates) / len(coordinates)
            lng = sum(item[1] for item in coordinates) / len(coordinates)
            return self._remember(key, lat=lat, lng=lng, precision="POSTAL", source="ZIPPOPOTAM")
        except (requests.RequestException, ValueError, TypeError) as exc:
            LOGGER.warning("Postal geocode failed postal=%s error=%s", postal_code, exc)
            return None

    def _resolve_search_city(self, city: str) -> dict[str, Any] | None:
        normalized = _normalized_city(city)
        if not normalized:
            return None
        key = f"city:DE:{normalized}"
        cached = self._cached(key)
        if cached:
            return cached
        try:
            self._city_limiter.wait()
            response = self._http.get(
                NOMINATIM_URL,
                params={
                    "q": f"{city}, Germany",
                    "format": "jsonv2",
                    "limit": 1,
                    "countrycodes": "de",
                },
                timeout=8,
            )
            response.raise_for_status()
            items = response.json()
            if not items:
                return None
            return self._remember(
                key,
                lat=float(items[0]["lat"]),
                lng=float(items[0]["lon"]),
                precision="SEARCH_AREA",
                source="NOMINATIM_CITY",
            )
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            LOGGER.warning("Search city geocode failed city=%s error=%s", city, exc)
            return None

    def feature_collection(self, search_run_id: int) -> dict[str, Any]:
        context = self.repository.search_context(search_run_id)
        items = context["items"]
        search_center = self._resolve_search_city(context.get("city") or "")

        postal_locations: dict[str, dict[str, Any] | None] = {}
        for item in items:
            postal = _postal_code(item)
            if postal and postal not in postal_locations:
                postal_locations[postal] = self._resolve_postal(postal)

        features = []
        unresolved = 0
        precision_counts: dict[str, int] = {}
        for item in items:
            postal = _postal_code(item)
            location = postal_locations.get(postal) if postal else None
            precision = "POSTAL"
            if location is None:
                location = search_center
                precision = "SEARCH_AREA"
            if location is None:
                unresolved += 1
                continue
            precision = str(location.get("precision") or precision)
            precision_counts[precision] = precision_counts.get(precision, 0) + 1
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(location["lng"]), float(location["lat"])],
                },
                "properties": {
                    "person_id": int(item["person_id"]),
                    "listing_id": int(item["listing_id"]),
                    "advert_id": str(item["advert_id"]),
                    "position": int(item["position"]),
                    "name": str(item.get("name") or "Unbekannt"),
                    "city": str(item.get("city") or context.get("city") or ""),
                    "postal_code": postal or "",
                    "precision": precision,
                    "thumbnail_url": str(item.get("thumbnail_url") or ""),
                },
            })

        center = None
        if search_center:
            center = [float(search_center["lng"]), float(search_center["lat"])]
        elif features:
            center = [
                sum(feature["geometry"]["coordinates"][0] for feature in features) / len(features),
                sum(feature["geometry"]["coordinates"][1] for feature in features) / len(features),
            ]

        return {
            "type": "FeatureCollection",
            "features": features,
            "meta": {
                "search_run_id": int(context["search_run_id"]),
                "center": center,
                "resolved_count": len(features),
                "unresolved_count": unresolved,
                "precision_counts": precision_counts,
            },
        }
