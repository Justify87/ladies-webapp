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
        raw_ids = payload.get("listing_ids") or []
        if not isinstance(raw_ids, list):
            raise ValidationError("listing_ids muss eine Liste sein")
        if len(raw_ids) > MAX_MAP_LISTINGS:
            raise ValidationError("Zu viele Treffer für die Kartenansicht")

        listing_ids: list[int] = []
        seen: set[int] = set()
        for raw in raw_ids:
            try:
                listing_id = int(raw)
            except (TypeError, ValueError):
                continue
            if listing_id > 0 and listing_id not in seen:
                seen.add(listing_id)
                listing_ids.append(listing_id)

        fallback_city = str(payload.get("fallback_city") or "").strip()[:160]
        return jsonify(service.feature_collection(listing_ids, fallback_city=fallback_city))

    return service
