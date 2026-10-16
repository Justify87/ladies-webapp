from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import sys
import tempfile
import threading
import types
from pathlib import Path


source = types.ModuleType("ladies_v9.source")


class FixedRateLimiter:
    def __init__(self, min_interval_seconds: float = 1.0):
        self.min_interval_seconds = min_interval_seconds

    def wait(self) -> float:
        return 0.0


source.FixedRateLimiter = FixedRateLimiter
ladies_v9 = types.ModuleType("ladies_v9")

orjson = types.ModuleType("orjson")
orjson.OPT_SORT_KEYS = 1
orjson.JSONDecodeError = json.JSONDecodeError
orjson.loads = lambda value: json.loads(value.decode("utf-8") if isinstance(value, bytes) else value)
orjson.dumps = lambda value, option=0: json.dumps(
    value,
    ensure_ascii=False,
    sort_keys=bool(option & orjson.OPT_SORT_KEYS),
    separators=(",", ":"),
).encode("utf-8")

requests = types.ModuleType("requests")


class RequestException(Exception):
    pass


class Session:
    def __init__(self):
        self.headers = {}

    def get(self, *args, **kwargs):
        raise AssertionError("Map behavior checks must not perform unexpected network I/O")


requests.RequestException = RequestException
requests.Session = Session
sys.modules.update({
    "ladies_v9": ladies_v9,
    "ladies_v9.source": source,
    "orjson": orjson,
    "requests": requests,
})

for package_name in ("ladies", "ladies.core", "ladies.core.services"):
    package = types.ModuleType(package_name)
    package.__path__ = []
    sys.modules[package_name] = package


def load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


locations = load_module("ladies.core.services.map_locations", "ladies/core/services/map_locations.py")
map_module = load_module("ladies.core.services.map", "ladies/core/services/map.py")
MapLocationResolver = locations.MapLocationResolver
MapService = map_module.MapService
city_key = locations._city_key
postal_key = locations._postal_key


class Repository:
    def __init__(self, rows):
        self.rows = list(rows)

    def map_items(self, listing_ids):
        wanted = set(listing_ids)
        return [dict(row) for row in self.rows if row["listing_id"] in wanted]


class Background:
    def __init__(self):
        self.submissions = []

    def submit(self, key, function, *args, **kwargs):
        self.submissions.append((key, function, args, kwargs))
        return object()


def row(listing_id, person_id, advert_id, *, city=None, postal_code=None, hidden_location=None):
    return {
        "listing_id": listing_id,
        "person_id": person_id,
        "advert_id": advert_id,
        "city": city,
        "postal_code": postal_code,
        "hidden_location": hidden_location,
        "price_60_cents": None,
    }


# Canonical snapshot location wins over the transient search-row city. Listings remain
# listing-based even when they belong to the same person, and refs are normal arrays.
with tempfile.TemporaryDirectory() as tmp:
    background = Background()
    resolver = MapLocationResolver(data_dir=tmp, background=background)
    resolver._remember(city_key("Markkleeberg"), lat=51.28, lng=12.37, precision="SEARCH_AREA", source="TEST")
    resolver._remember(city_key("Grimma"), lat=51.24, lng=12.72, precision="SEARCH_AREA", source="TEST")
    service = MapService(
        Repository([
            row(1, 101, "a", city=None),
            row(2, 101, "b", city="Grimma", hidden_location="Musterstraße 12, Grimma"),
        ]),
        resolver=resolver,
    )
    points = service.feature_collection([
        {"listing_id": 1, "city": "Markkleeberg"},
        {"listing_id": 2, "city": "Leipzig"},
        {"listing_id": 2, "city": "Leipzig"},
    ])
    keys = {feature["properties"]["location_key"] for feature in points["features"]}
    assert city_key("Markkleeberg") in keys
    assert city_key("Grimma") in keys
    assert city_key("Leipzig") not in keys
    assert points["meta"]["listing_count"] == 2
    assert points["meta"]["mapped_listing_count"] == 2
    refs = [ref for feature in points["features"] for ref in feature["properties"]["listing_refs"]]
    assert sorted(ref["listing_id"] for ref in refs) == [1, 2]
    refs_by_listing = {ref["listing_id"]: ref for ref in refs}
    assert refs_by_listing[1]["address_label"] == "Markkleeberg"
    assert refs_by_listing[2]["address_label"] == "Musterstraße 12, Grimma"
    assert all("listing_refs_json" not in feature["properties"] for feature in points["features"])
    assert not background.submissions


# A postcode is the only initial lookup. City geocoding is a fallback after a definite
# postcode miss, not parallel work.
with tempfile.TemporaryDirectory() as tmp:
    background = Background()
    resolver = MapLocationResolver(data_dir=tmp, background=background)
    service = MapService(
        Repository([row(3, 303, "c", city="Leipzig", postal_code="04109")]),
        resolver=resolver,
    )
    points = service.feature_collection([{"listing_id": 3, "city": "Leipzig"}])
    assert points["meta"]["pending_count"] == 1
    assert len(background.submissions) == 1
    pending = background.submissions[0][2][0]
    assert [spec["kind"] for spec in pending] == ["postal"]
    assert pending[0]["value"] == "04109"
    assert pending[0]["fallback_city"] == "Leipzig"


class Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RequestException(str(self.status_code))

    def json(self):
        return self.payload


class ScriptedSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


# Postal 404 falls through to the city in the same background job. Stored hidden
# address text must never be transmitted to either external provider.
with tempfile.TemporaryDirectory() as tmp:
    resolver = MapLocationResolver(data_dir=tmp, background=Background())
    resolver._http = ScriptedSession([
        Response(404, {}),
        Response(200, [{"lat": "51.3397", "lon": "12.3731"}]),
    ])
    resolver._resolve_missing([{
        "key": postal_key("99999"),
        "kind": "postal",
        "value": "99999",
        "fallback_city_key": city_key("Leipzig"),
        "fallback_city": "Leipzig",
    }])
    assert resolver._cached_record(postal_key("99999"))["status"] == "NOT_FOUND"
    assert resolver._resolved_location(city_key("Leipzig"))["precision"] == "SEARCH_AREA"
    assert len(resolver._http.calls) == 2
    serialized_calls = json.dumps(resolver._http.calls, ensure_ascii=False)
    assert "99999" in serialized_calls and "Leipzig" in serialized_calls
    assert "Musterstraße" not in serialized_calls


# Negative geocoder results have a TTL. An old NOT_FOUND record becomes eligible for
# resolution again instead of permanently suppressing a location.
with tempfile.TemporaryDirectory() as tmp:
    background = Background()
    resolver = MapLocationResolver(data_dir=tmp, background=background)
    key = city_key("Leipzig")
    resolver._cache[key] = {
        "status": "NOT_FOUND",
        "source": "TEST",
        "updated_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
    }
    assert resolver._cached_record(key) is None
    service = MapService(Repository([row(4, 404, "d", city="Leipzig")]), resolver=resolver)
    points = service.feature_collection([{"listing_id": 4, "city": "Leipzig"}])
    assert points["meta"]["pending_count"] == 1
    pending = background.submissions[0][2][0]
    assert [spec["kind"] for spec in pending] == ["city"]


# Invalid cached coordinates are discarded on startup and never emitted to MapLibre.
with tempfile.TemporaryDirectory() as tmp:
    cache_path = Path(tmp) / "map-location-cache.json"
    cache_path.write_text(json.dumps({
        postal_key("04109"): {
            "status": "RESOLVED",
            "lat": 14713.0,
            "lng": 51.342,
            "precision": "POSTAL",
            "source": "TEST",
        }
    }), encoding="utf-8")
    background = Background()
    resolver = MapLocationResolver(data_dir=tmp, background=background)
    service = MapService(
        Repository([row(5, 505, "e", city="Leipzig", postal_code="04109")]),
        resolver=resolver,
    )
    assert resolver._cached_record(postal_key("04109")) is None
    points = service.feature_collection([{"listing_id": 5, "city": "Leipzig"}])
    assert points["features"] == []
    assert points["meta"]["pending_count"] == 1
    try:
        resolver._remember(postal_key("04109"), lat=14713.0, lng=51.342, precision="POSTAL", source="TEST")
        raise AssertionError("invalid coordinates must not be cached")
    except ValueError:
        pass


# Cache persistence is atomic under concurrent writers.
with tempfile.TemporaryDirectory() as tmp:
    resolver = MapLocationResolver(data_dir=tmp, background=Background())

    def write(key, lat):
        resolver._remember(key, lat=lat, lng=12.0, precision="SEARCH_AREA", source="TEST")
        resolver._save_cache()

    threads = [
        threading.Thread(target=write, args=(city_key("Leipzig"), 51.3)),
        threading.Thread(target=write, args=(city_key("Grimma"), 51.2)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    saved = json.loads((Path(tmp) / "map-location-cache.json").read_text(encoding="utf-8"))
    assert city_key("Leipzig") in saved
    assert city_key("Grimma") in saved


# Price projection remains independent from map persistence details.
projection_spec = importlib.util.spec_from_file_location("map_projection", "ladies/pricing/map_projection.py")
assert projection_spec and projection_spec.loader
projection = importlib.util.module_from_spec(projection_spec)
projection_spec.loader.exec_module(projection)
resolved = projection.resolve_map_price_60_cents(
    {11: 1, 22: 1, 33: 1, 44: 1, 55: 1},
    {
        (1, None): (200, "EUR"),
        (1, 11): (101, "EUR"),
        (1, 33): (301, "EUR"),
        (1, 44): (401, "USD"),
        (1, 55): (501, "EUR"),
    },
    {
        101: [12000],
        200: [9000],
        300: [15000],
        401: [17000],
        501: [10000, 14000],
    },
)
assert resolved[11] == 12000
assert resolved[22] == 9000
assert 33 not in resolved
assert 44 not in resolved
assert resolved[55] == 12000

print("map backend behavior checks passed")
