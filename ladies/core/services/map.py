from __future__ import annotations

from datetime import datetime, timezone
import hashlib
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


def _city_key(city: str) -> str:
    return f"city:DE:{_normalized_city(city)}"


def _postal_key(postal_code: str) -> str:
    return f"postal:DE:{postal_code}"


class MapService:
    """Build cached, privacy-conscious map data for the current active results.

    The request path reads canonical local profile data plus the current search-row
    city fallback. Missing centroids are resolved only by the dedicated map
    background executor. Hidden addresses never leave the process; only a five-digit
    postcode or, when no usable postcode exists, a city name may reach a provider.
    """

    def __init__(self, repository, *, data_dir: str | Path, background):
        self.repository = repository
        self.background = background
        self.cache_path = Path(data_dir) / "map-location-cache.json"
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._cache = self._read_cache()
        self._resolving: set[str] = set()
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
            payload = orjson.dumps(self._cache, option=orjson.OPT_SORT_KEYS)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_bytes(payload)
            tmp.replace(self.cache_path)

    def _cached_record(self, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        with self._lock:
            value = self._cache.get(key)
            return dict(value) if isinstance(value, dict) else None

    def _resolved_location(self, key: str | None) -> dict[str, Any] | None:
        value = self._cached_record(key)
        if not value or value.get("status") != "RESOLVED":
            return None
        try:
            return {
                **value,
                "lat": float(value["lat"]),
                "lng": float(value["lng"]),
            }
        except (KeyError, TypeError, ValueError):
            return None

    def _remember(self, key: str, *, lat: float, lng: float, precision: str, source: str) -> None:
        with self._lock:
            self._cache[key] = {
                "status": "RESOLVED",
                "lat": float(lat),
                "lng": float(lng),
                "precision": str(precision),
                "source": str(source),
                "updated_at": _now_iso(),
            }

    def _remember_not_found(self, key: str, *, source: str) -> None:
        with self._lock:
            self._cache[key] = {
                "status": "NOT_FOUND",
                "source": str(source),
                "updated_at": _now_iso(),
            }

    def _resolve_postal(self, spec: dict[str, str]) -> None:
        postal_code = spec["value"]
        response = self._http.get(ZIPPOPOTAM_URL.format(postal_code=postal_code), timeout=6)
        if response.status_code == 404:
            self._remember_not_found(spec["key"], source="ZIPPOPOTAM")
            return
        response.raise_for_status()
        data = response.json()
        coordinates: list[tuple[float, float]] = []
        for place in data.get("places") or []:
            try:
                coordinates.append((float(place["latitude"]), float(place["longitude"])))
            except (KeyError, TypeError, ValueError):
                continue
        if not coordinates:
            self._remember_not_found(spec["key"], source="ZIPPOPOTAM")
            return
        self._remember(
            spec["key"],
            lat=sum(item[0] for item in coordinates) / len(coordinates),
            lng=sum(item[1] for item in coordinates) / len(coordinates),
            precision="POSTAL",
            source="ZIPPOPOTAM",
        )

    def _resolve_city(self, spec: dict[str, str]) -> None:
        self._city_limiter.wait()
        response = self._http.get(
            NOMINATIM_URL,
            params={
                "q": f"{spec['value']}, Germany",
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "de",
            },
            timeout=8,
        )
        response.raise_for_status()
        items = response.json()
        if not items:
            self._remember_not_found(spec["key"], source="NOMINATIM_CITY")
            return
        self._remember(
            spec["key"],
            lat=float(items[0]["lat"]),
            lng=float(items[0]["lon"]),
            precision="SEARCH_AREA",
            source="NOMINATIM_CITY",
        )

    def _resolve_missing(self, specs: list[dict[str, str]]) -> None:
        keys = [spec["key"] for spec in specs]
        try:
            ordered = sorted(specs, key=lambda spec: 0 if spec["kind"] == "postal" else 1)
            for spec in ordered:
                if self._cached_record(spec["key"]) is not None:
                    continue
                try:
                    if spec["kind"] == "postal":
                        self._resolve_postal(spec)
                    else:
                        self._resolve_city(spec)
                except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
                    LOGGER.warning("Map geocode failed key=%s error=%s", spec["key"], exc)
            try:
                self._save_cache()
            except OSError:
                LOGGER.warning("Map geocode cache could not be written path=%s", self.cache_path)
        finally:
            with self._lock:
                self._resolving.difference_update(keys)

    def _schedule_missing(self, specs: dict[str, dict[str, str]]) -> int:
        with self._lock:
            pending = [
                spec for key, spec in specs.items()
                if key not in self._cache and key not in self._resolving
            ]
            self._resolving.update(spec["key"] for spec in pending)
        if pending:
            digest = hashlib.sha1("|".join(sorted(spec["key"] for spec in pending)).encode("utf-8")).hexdigest()[:16]
            future = self.background.submit(f"map-geocode:{digest}", self._resolve_missing, pending)
            if future is None:
                with self._lock:
                    self._resolving.difference_update(spec["key"] for spec in pending)
        with self._lock:
            return sum(1 for key in specs if key not in self._cache or key in self._resolving)

    def _resolution_specs(self, prepared: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
        specs: dict[str, dict[str, str]] = {}
        for item in prepared:
            postal_key = item["postal_key"]
            city_key = item["city_key"]
            if postal_key:
                postal_record = self._cached_record(postal_key)
                if postal_record is None:
                    specs[postal_key] = {
                        "key": postal_key,
                        "kind": "postal",
                        "value": item["postal"],
                    }
                elif postal_record.get("status") == "NOT_FOUND" and city_key and self._cached_record(city_key) is None:
                    specs[city_key] = {
                        "key": city_key,
                        "kind": "city",
                        "value": item["city"],
                    }
            elif city_key and self._cached_record(city_key) is None:
                specs[city_key] = {
                    "key": city_key,
                    "kind": "city",
                    "value": item["city"],
                }
        return specs

    def feature_collection(self, active_items: list[dict[str, Any]]) -> dict[str, Any]:
        listing_ids: list[int] = []
        active_city_by_listing: dict[int, str] = {}
        seen: set[int] = set()
        for raw in active_items:
            if not isinstance(raw, dict):
                continue
            try:
                listing_id = int(raw.get("listing_id"))
            except (TypeError, ValueError):
                continue
            if listing_id <= 0:
                continue
            city = str(raw.get("city") or "").strip()[:160]
            if listing_id not in seen:
                seen.add(listing_id)
                listing_ids.append(listing_id)
                active_city_by_listing[listing_id] = city
            elif city and not active_city_by_listing.get(listing_id):
                active_city_by_listing[listing_id] = city

        items = self.repository.map_items(listing_ids)
        prepared: list[dict[str, Any]] = []
        for item in items:
            postal = _postal_code(item)
            city = str(item.get("city") or active_city_by_listing.get(int(item["listing_id"])) or "").strip()
            prepared.append({
                **item,
                "postal": postal,
                "city": city,
                "postal_key": _postal_key(postal) if postal else None,
                "city_key": _city_key(city) if city else None,
            })

        pending_count = self._schedule_missing(self._resolution_specs(prepared))
        groups: dict[str, dict[str, Any]] = {}
        unresolved = 0

        for item in prepared:
            location = self._resolved_location(item["postal_key"])
            location_key = item["postal_key"]
            precision = "POSTAL"
            if location is None and item["postal_key"]:
                postal_record = self._cached_record(item["postal_key"])
                if postal_record and postal_record.get("status") == "NOT_FOUND":
                    location = self._resolved_location(item["city_key"])
                    location_key = item["city_key"]
                    precision = "SEARCH_AREA"
            elif location is None:
                location = self._resolved_location(item["city_key"])
                location_key = item["city_key"]
                precision = "SEARCH_AREA"

            if location is None or not location_key:
                unresolved += 1
                continue

            precision = str(location.get("precision") or precision)
            label = (
                f"{item['postal']} · {item['city']}" if precision == "POSTAL" and item["postal"]
                else item["city"] or "Suchgebiet"
            )
            group = groups.setdefault(location_key, {
                "lat": float(location["lat"]),
                "lng": float(location["lng"]),
                "precision": precision,
                "postal_code": item["postal"] or "",
                "city": item["city"],
                "label": label,
                "profiles": [],
            })
            group["profiles"].append({
                "person_id": int(item["person_id"]),
                "listing_id": int(item["listing_id"]),
                "advert_id": str(item["advert_id"]),
            })

        features = []
        for location_key, group in groups.items():
            profiles = group["profiles"]
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [group["lng"], group["lat"]],
                },
                "properties": {
                    "location_key": location_key,
                    "location_label": group["label"],
                    "city": group["city"],
                    "postal_code": group["postal_code"],
                    "precision": group["precision"],
                    "profile_count": len(profiles),
                    "profiles_json": orjson.dumps(profiles).decode("utf-8"),
                },
            })

        center = None
        if features:
            center = [
                sum(feature["geometry"]["coordinates"][0] for feature in features) / len(features),
                sum(feature["geometry"]["coordinates"][1] for feature in features) / len(features),
            ]

        return {
            "type": "FeatureCollection",
            "features": features,
            "meta": {
                "center": center,
                "profile_count": len(items),
                "mapped_profile_count": sum(feature["properties"]["profile_count"] for feature in features),
                "unresolved_profile_count": unresolved,
                "location_count": len(features),
                "pending_count": pending_count,
            },
        }
