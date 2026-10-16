from __future__ import annotations

import json
import runpy
import subprocess
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
package = types.ModuleType("ladies_v9")

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
    "ladies_v9": package,
    "ladies_v9.source": source,
    "orjson": orjson,
    "requests": requests,
})

module = runpy.run_path("ladies/core/services/map.py")
MapService = module["MapService"]
city_key = module["_city_key"]
postal_key = module["_postal_key"]


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
    }


# Map identity is listing-scoped; canonical snapshot city overrides search-result city.
with tempfile.TemporaryDirectory() as tmp:
    background = Background()
    service = MapService(
        Repository([
            row(1, 101, "a", city=None),
            row(2, 101, "b", city="Grimma", hidden_location="Musterstraße 12, Grimma"),
        ]),
        data_dir=tmp,
        background=background,
    )
    service._remember(city_key("Markkleeberg"), lat=51.28, lng=12.37, precision="SEARCH_AREA", source="TEST")
    service._remember(city_key("Grimma"), lat=51.24, lng=12.72, precision="SEARCH_AREA", source="TEST")
    points = service.feature_collection([
        {"listing_id": 1, "city": "Markkleeberg"},
        {"listing_id": 2, "city": "Leipzig"},
    ])
    keys = {feature["properties"]["location_key"] for feature in points["features"]}
    assert city_key("Markkleeberg") in keys
    assert city_key("Grimma") in keys
    assert city_key("Leipzig") not in keys
    assert points["meta"]["listing_count"] == 2
    assert points["meta"]["mapped_listing_count"] == 2
    refs = [
        ref
        for feature in points["features"]
        for ref in json.loads(feature["properties"]["listing_refs_json"])
    ]
    assert sorted(ref["listing_id"] for ref in refs) == [1, 2]
    refs_by_listing = {ref["listing_id"]: ref for ref in refs}
    assert refs_by_listing[1]["address_label"] == "Markkleeberg"
    assert refs_by_listing[2]["address_label"] == "Musterstraße 12, Grimma"
    assert all("profile_count" not in feature["properties"] for feature in points["features"])
    assert all("profiles_json" not in feature["properties"] for feature in points["features"])
    assert not background.submissions


# Postal lookup has priority over less precise city geocoding.
with tempfile.TemporaryDirectory() as tmp:
    background = Background()
    service = MapService(
        Repository([row(3, 303, "c", city="Leipzig", postal_code="04109")]),
        data_dir=tmp,
        background=background,
    )
    points = service.feature_collection([{"listing_id": 3, "city": "Leipzig"}])
    assert points["meta"]["pending_count"] == 1
    assert len(background.submissions) == 1
    pending = background.submissions[0][2][0]
    assert [spec["kind"] for spec in pending] == ["postal"]
    assert pending[0]["value"] == "04109"
    assert pending[0]["fallback_city"] == "Leipzig"


# A definitive postal miss resolves its city fallback in the same background job.
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


with tempfile.TemporaryDirectory() as tmp:
    service = MapService(Repository([]), data_dir=tmp, background=Background())
    service._http = ScriptedSession([
        Response(404, {}),
        Response(200, [{"lat": "51.3397", "lon": "12.3731"}]),
    ])
    service._resolve_missing([{
        "key": postal_key("99999"),
        "kind": "postal",
        "value": "99999",
        "fallback_city_key": city_key("Leipzig"),
        "fallback_city": "Leipzig",
    }])
    assert service._cached_record(postal_key("99999"))["status"] == "NOT_FOUND"
    assert service._resolved_location(city_key("Leipzig"))["precision"] == "SEARCH_AREA"
    assert len(service._http.calls) == 2


# A known unavailable postcode schedules only its city fallback.
with tempfile.TemporaryDirectory() as tmp:
    background = Background()
    service = MapService(
        Repository([row(4, 404, "d", city="Leipzig", postal_code="04109")]),
        data_dir=tmp,
        background=background,
    )
    service._remember_not_found(postal_key("04109"), source="TEST")
    points = service.feature_collection([{"listing_id": 4, "city": "Leipzig"}])
    assert points["meta"]["pending_count"] == 1
    pending = background.submissions[0][2][0]
    assert [spec["kind"] for spec in pending] == ["city"]
    assert pending[0]["value"] == "Leipzig"


# Invalid coordinates are discarded before they can reach GeoJSON or MapLibre.
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
    service = MapService(
        Repository([row(5, 505, "e", city="Leipzig", postal_code="04109")]),
        data_dir=tmp,
        background=background,
    )
    assert service._cached_record(postal_key("04109")) is None
    points = service.feature_collection([{"listing_id": 5, "city": "Leipzig"}])
    assert points["features"] == []
    assert points["meta"]["pending_count"] == 1
    pending = background.submissions[0][2][0]
    assert [spec["kind"] for spec in pending] == ["postal"]
    assert pending[0]["value"] == "04109"
    try:
        service._remember(postal_key("04109"), lat=14713.0, lng=51.342, precision="POSTAL", source="TEST")
        raise AssertionError("invalid coordinates must not be cached")
    except ValueError:
        pass


# Cache persistence remains atomic under concurrent writers.
with tempfile.TemporaryDirectory() as tmp:
    service = MapService(Repository([]), data_dir=tmp, background=Background())

    def write(key, lat):
        service._remember(key, lat=lat, lng=12.0, precision="SEARCH_AREA", source="TEST")
        service._save_cache()

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


# Listing-scoped pricing takes precedence; person-wide pricing is a fallback only.
projection = runpy.run_path("ladies/pricing/map_projection.py")
resolve_map_price_60_cents = projection["resolve_map_price_60_cents"]
resolved = resolve_map_price_60_cents(
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


for check in (
    "tools/check_profile_request_ui.mjs",
    "tools/check_favorites_redesign.mjs",
    "tools/check_map_price_persistence.mjs",
):
    subprocess.run(
        ["node", "--experimental-default-type=module", check],
        check=True,
    )

print("map view backend regression checks passed")
