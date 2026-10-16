from __future__ import annotations

from flask import Flask, jsonify, request

from .core.services.map import MapService
from .core.services.map_locations import MapLocationResolver
from .errors import ValidationError


MAX_MAP_LISTINGS = 2500


def install_map_view(app: Flask, *, background, data_dir) -> MapService:
    resolver = MapLocationResolver(data_dir=data_dir, background=background)
    service = MapService(app.extensions["ladies_repositories"].map, resolver=resolver)
    app.extensions["map_location_resolver"] = resolver
    app.extensions["map_service"] = service

    @app.post("/api/map-points")
    def api_map_points():
        payload = request.get_json(silent=True) or {}
        raw_items = payload.get("items") or []
        if not isinstance(raw_items, list):
            raise ValidationError("items muss eine Liste sein")
        if len(raw_items) > MAX_MAP_LISTINGS:
            raise ValidationError("Zu viele Treffer für die Kartenansicht")
        return jsonify(service.feature_collection(raw_items))

    return service
