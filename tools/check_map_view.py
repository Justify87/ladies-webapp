from __future__ import annotations

import json
import runpy
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
        raise AssertionError("Map behavior checks must not perform network I/O")


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


# A not-yet-ingested listing must use its own active search-result city, while a
# canonical snapshot city remains authoritative when it exists.
with tempfile.TemporaryDirectory() as tmp:
    background = Background()
    service = MapService(
        Repository([
            row(1, 101, "a", city=None),
            row(2, 202, "b", city="Grimma"),
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
    assert not background.submissions


# A postcode is the first and only external lookup while it is unresolved. City
# geocoding must not run eagerly in parallel with a more precise postal lookup.
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


# Only a definitively unavailable postcode may fall back to the city resolver.
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


# Cache persistence must remain atomic under concurrent writers. The service uses a
# shared temp path, so serialization and replace belong to the same critical section.
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

print("map view backend regression checks passed")
