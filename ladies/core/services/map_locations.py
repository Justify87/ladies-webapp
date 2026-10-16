from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
NOT_FOUND_TTL = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _normalized_city(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _postal_code(item: dict[str, Any]) -> str | None:
    for value in (item.get("postal_code"), item.get("hidden_location")):
        match = POSTAL_RE.search(str(value or ""))
        if match:
            return match.group(1)
    return None


def _address_label(item: dict[str, Any], *, postal: str | None, city: str) -> str:
    hidden = " ".join(str(item.get("hidden_location") or "").split()).strip()
    if hidden:
        return hidden[:160]
    if postal and city:
        return f"{postal} {city}"[:160]
    return str(postal or city or "")[:160]


def _city_key(city: str) -> str:
    return f"city:DE:{_normalized_city(city)}"


def _postal_key(postal_code: str) -> str:
    return f"postal:DE:{postal_code}"


def _valid_coordinates(lat: Any, lng: Any) -> bool:
    try:
        latitude = float(lat)
        longitude = float(lng)
    except (TypeError, ValueError):
        return False
    return -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0


class MapLocationResolver:
    """Resolve and cache coarse map locations without exposing stored addresses."""

    def __init__(self, *, data_dir: str | Path, background, not_found_ttl: timedelta = NOT_FOUND_TTL):
        self.background = background
        self.not_found_ttl = not_found_ttl
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
        except (OSError, ValueError, TypeError, orjson.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}

        valid: dict[str, dict[str, Any]] = {}
        invalid_keys: list[str] = []
        for raw_key, raw_record in value.items():
            key = str(raw_key)
            if not isinstance(raw_record, dict):
                invalid_keys.append(key)
                continue
            record = dict(raw_record)
            if record.get("status") == "RESOLVED" and not _valid_coordinates(record.get("lat"), record.get("lng")):
                invalid_keys.append(key)
                continue
            valid[key] = record

        if invalid_keys:
            LOGGER.warning(
                "Discarded %d invalid map cache record(s): %s",
                len(invalid_keys),
                ", ".join(invalid_keys[:8]),
            )
        return valid

    def _save_cache(self) -> None:
        with self._lock:
            payload = orjson.dumps(self._cache, option=orjson.OPT_SORT_KEYS)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_bytes(payload)
            tmp.replace(self.cache_path)

    def _not_found_expired(self, record: dict[str, Any]) -> bool:
        if record.get("status") != "NOT_FOUND":
            return False
        try:
            updated = datetime.fromisoformat(str(record.get("updated_at") or ""))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return True
        return _now() - updated.astimezone(timezone.utc) >= self.not_found_ttl

    def _cached_record(self, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        with self._lock:
            value = self._cache.get(key)
            if not isinstance(value, dict):
                return None
            if self._not_found_expired(value):
                self._cache.pop(key, None)
                return None
            return dict(value)

    def _drop_invalid_cached_location(self, key: str, value: dict[str, Any]) -> None:
        LOGGER.warning(
            "Discarding invalid resolved map coordinate key=%s lat=%r lng=%r",
            key,
            value.get("lat"),
            value.get("lng"),
        )
        with self._lock:
            current = self._cache.get(key)
            if current == value:
                self._cache.pop(key, None)

    def _resolved_location(self, key: str | None) -> dict[str, Any] | None:
        value = self._cached_record(key)
        if not value or value.get("status") != "RESOLVED":
            return None
        if not _valid_coordinates(value.get("lat"), value.get("lng")):
            if key:
                self._drop_invalid_cached_location(key, value)
            return None
        return {**value, "lat": float(value["lat"]), "lng": float(value["lng"])}

    def _remember(self, key: str, *, lat: float, lng: float, precision: str, source: str) -> None:
        if not _valid_coordinates(lat, lng):
            raise ValueError(f"Ungültige Kartenkoordinate für {key}: lat={lat!r} lng={lng!r}")
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

    def prepare(self, item: dict[str, Any], fallback_city: str = "") -> dict[str, Any]:
        postal = _postal_code(item)
        city = str(item.get("city") or fallback_city or "").strip()[:160]
        return {
            **item,
            "postal": postal,
            "city": city,
            "address_label": _address_label(item, postal=postal, city=city),
            "postal_key": _postal_key(postal) if postal else None,
            "city_key": _city_key(city) if city else None,
        }

    def _resolve_postal(self, spec: dict[str, str]) -> bool:
        postal_code = spec["value"]
        response = self._http.get(ZIPPOPOTAM_URL.format(postal_code=postal_code), timeout=6)
        if response.status_code == 404:
            self._remember_not_found(spec["key"], source="ZIPPOPOTAM")
            return False
        response.raise_for_status()
        data = response.json()
        coordinates: list[tuple[float, float]] = []
        for place in data.get("places") or []:
            try:
                lat = float(place["latitude"])
                lng = float(place["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if _valid_coordinates(lat, lng):
                coordinates.append((lat, lng))
            else:
                LOGGER.warning(
                    "Ignoring invalid ZIPPOPOTAM coordinate postal=%s lat=%r lng=%r",
                    postal_code,
                    place.get("latitude"),
                    place.get("longitude"),
                )
        if not coordinates:
            self._remember_not_found(spec["key"], source="ZIPPOPOTAM")
            return False
        self._remember(
            spec["key"],
            lat=sum(item[0] for item in coordinates) / len(coordinates),
            lng=sum(item[1] for item in coordinates) / len(coordinates),
            precision="POSTAL",
            source="ZIPPOPOTAM",
        )
        return True

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
                        postal_resolved = self._resolve_postal(spec)
                        fallback_key = spec.get("fallback_city_key")
                        fallback_value = spec.get("fallback_city")
                        if not postal_resolved and fallback_key and fallback_value and self._cached_record(fallback_key) is None:
                            self._resolve_city({"key": fallback_key, "kind": "city", "value": fallback_value})
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

    def _resolution_specs(self, prepared: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
        specs: dict[str, dict[str, str]] = {}
        for item in prepared:
            postal_key = item["postal_key"]
            city_key = item["city_key"]
            if postal_key:
                postal_record = self._cached_record(postal_key)
                if postal_record is None:
                    spec = {"key": postal_key, "kind": "postal", "value": item["postal"]}
                    if city_key and item["city"]:
                        spec["fallback_city_key"] = city_key
                        spec["fallback_city"] = item["city"]
                    specs[postal_key] = spec
                elif postal_record.get("status") == "NOT_FOUND" and city_key:
                    if self._cached_record(city_key) is None:
                        specs[city_key] = {"key": city_key, "kind": "city", "value": item["city"]}
            elif city_key and self._cached_record(city_key) is None:
                specs[city_key] = {"key": city_key, "kind": "city", "value": item["city"]}
        return specs

    def schedule(self, prepared: list[dict[str, Any]]) -> int:
        specs = self._resolution_specs(prepared)
        with self._lock:
            pending = [spec for key, spec in specs.items() if key not in self._cache and key not in self._resolving]
            self._resolving.update(spec["key"] for spec in pending)
        if pending:
            digest = hashlib.sha1("|".join(sorted(spec["key"] for spec in pending)).encode("utf-8")).hexdigest()[:16]
            future = self.background.submit(f"map-geocode:{digest}", self._resolve_missing, pending)
            if future is None:
                with self._lock:
                    self._resolving.difference_update(spec["key"] for spec in pending)
        with self._lock:
            return sum(1 for key in specs if key not in self._cache or key in self._resolving)

    def resolved_for(self, item: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        postal_key = item.get("postal_key")
        city_key = item.get("city_key")
        location = self._resolved_location(postal_key)
        location_key = postal_key
        if location is None and postal_key:
            postal_record = self._cached_record(postal_key)
            if postal_record and postal_record.get("status") == "NOT_FOUND":
                location = self._resolved_location(city_key)
                location_key = city_key
        elif location is None:
            location = self._resolved_location(city_key)
            location_key = city_key
        if location is None or not location_key:
            return None
        return str(location_key), location
