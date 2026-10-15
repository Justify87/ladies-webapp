from __future__ import annotations

from flask import Flask, jsonify, request

from .core.services.map import MapService
from .errors import ValidationError


MAX_MAP_LISTINGS = 2500


def install_map_view(app: Flask, *, background, data_dir) -> MapService:
    service = MapService(
        app.extensions["ladies_repositories"].map,
        data_dir=data_dir,
        background=background,
    )
    app.extensions["map_service"] = service

    @app.post("/api/map-points")
    def api_map_points():
        payload = request.get_json(silent=True) or {}
        raw_items = payload.get("items") or []
        if not isinstance(raw_items, list):
            raise ValidationError("items muss eine Liste sein")
        if len(raw_items) > MAX_MAP_LISTINGS:
            raise ValidationError("Zu viele Treffer für die Kartenansicht")

        active_items: list[dict] = []
        index_by_listing: dict[int, int] = {}
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            try:
                listing_id = int(raw.get("listing_id"))
            except (TypeError, ValueError):
                continue
            if listing_id <= 0:
                continue
            city = str(raw.get("city") or "").strip()[:160]
            existing_index = index_by_listing.get(listing_id)
            if existing_index is None:
                index_by_listing[listing_id] = len(active_items)
                active_items.append({"listing_id": listing_id, "city": city})
            elif city and not active_items[existing_index]["city"]:
                active_items[existing_index]["city"] = city

        return jsonify(service.feature_collection(active_items))

    return service
