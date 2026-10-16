from __future__ import annotations

import logging
from typing import Any

from .map_locations import MapLocationResolver, _valid_coordinates


LOGGER = logging.getLogger("ladies_explorer.map")


class MapService:
    """Project active listings onto cached coarse locations for the map UI."""

    def __init__(self, repository, *, resolver: MapLocationResolver):
        self.repository = repository
        self.resolver = resolver

    @staticmethod
    def _active_listing_request(active_items: list[dict[str, Any]]) -> tuple[list[int], dict[int, str]]:
        listing_ids: list[int] = []
        city_by_listing: dict[int, str] = {}
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
                city_by_listing[listing_id] = city
            elif city and not city_by_listing.get(listing_id):
                city_by_listing[listing_id] = city
        return listing_ids, city_by_listing

    def feature_collection(self, active_items: list[dict[str, Any]]) -> dict[str, Any]:
        listing_ids, active_city_by_listing = self._active_listing_request(active_items)
        items = self.repository.map_items(listing_ids)
        prepared = [
            self.resolver.prepare(item, active_city_by_listing.get(int(item["listing_id"]), ""))
            for item in items
        ]
        pending_count = self.resolver.schedule(prepared)
        groups: dict[str, dict[str, Any]] = {}
        unresolved = 0

        for item in prepared:
            resolved = self.resolver.resolved_for(item)
            if resolved is None:
                unresolved += 1
                continue
            location_key, location = resolved
            precision = str(location.get("precision") or "SEARCH_AREA")
            label = (
                f"{item['postal']} · {item['city']}"
                if precision == "POSTAL" and item["postal"]
                else item["city"] or "Suchgebiet"
            )
            group = groups.setdefault(location_key, {
                "lat": float(location["lat"]),
                "lng": float(location["lng"]),
                "precision": precision,
                "postal_code": item["postal"] or "",
                "city": item["city"],
                "label": label,
                "listings": [],
            })
            group["listings"].append({
                "person_id": int(item["person_id"]),
                "listing_id": int(item["listing_id"]),
                "advert_id": str(item["advert_id"]),
                "address_label": item["address_label"],
                "price_60_cents": item.get("price_60_cents"),
            })

        features = []
        for location_key, group in groups.items():
            if not _valid_coordinates(group["lat"], group["lng"]):
                LOGGER.error(
                    "Skipping invalid map feature key=%s lat=%r lng=%r",
                    location_key,
                    group["lat"],
                    group["lng"],
                )
                unresolved += len(group["listings"])
                continue
            listings = group["listings"]
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [group["lng"], group["lat"]]},
                "properties": {
                    "location_key": location_key,
                    "location_label": group["label"],
                    "city": group["city"],
                    "postal_code": group["postal_code"],
                    "precision": group["precision"],
                    "listing_count": len(listings),
                    "listing_refs": listings,
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
                "listing_count": len(items),
                "mapped_listing_count": sum(feature["properties"]["listing_count"] for feature in features),
                "unresolved_listing_count": unresolved,
                "location_count": len(features),
                "pending_count": pending_count,
            },
        }
