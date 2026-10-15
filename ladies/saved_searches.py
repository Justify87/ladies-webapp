from __future__ import annotations

from flask import Flask, jsonify, request

from .core.services.saved_searches import SavedSearchScheduler, SavedSearchService
from .core.services.search import SearchService
from .errors import NotFoundError, ValidationError
from .source import build_session


def install_saved_searches(
    app: Flask,
    *,
    background,
    default_city: str,
    default_radius: int,
) -> SavedSearchScheduler:
    repositories = app.extensions["ladies_repositories"]
    search = SearchService(
        repositories.search,
        build_session(),
        default_city=default_city,
        default_radius=default_radius,
    )
    service = SavedSearchService(repositories.saved_searches, search)
    scheduler = SavedSearchScheduler(service, background)
    app.extensions["saved_search_service"] = service
    app.extensions["saved_search_scheduler"] = scheduler

    @app.get("/api/saved-searches")
    def api_saved_searches():
        return jsonify(items=service.list())

    @app.post("/api/saved-searches")
    def api_saved_search_create():
        payload = request.get_json(silent=True) or {}
        try:
            items = service.create(name=payload.get("name"), filters=payload.get("filters"))
            return jsonify(items=items)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    @app.put("/api/saved-searches/<saved_id>")
    def api_saved_search_update(saved_id: str):
        payload = request.get_json(silent=True) or {}
        try:
            items, changed = service.update(saved_id, name=payload.get("name"), filters=payload.get("filters"))
            return jsonify(items=items, filters_changed=changed)
        except KeyError as exc:
            raise NotFoundError("Gespeicherte Suche nicht gefunden") from exc
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    @app.delete("/api/saved-searches/<saved_id>")
    def api_saved_search_delete(saved_id: str):
        try:
            return jsonify(items=service.delete(saved_id))
        except KeyError as exc:
            raise NotFoundError("Gespeicherte Suche nicht gefunden") from exc

    @app.post("/api/saved-searches/<saved_id>/run")
    def api_saved_search_run(saved_id: str):
        try:
            return jsonify(**service.run(saved_id, mark_seen=True))
        except KeyError as exc:
            raise NotFoundError("Gespeicherte Suche nicht gefunden") from exc

    @app.get("/api/saved-searches/<saved_id>/results")
    def api_saved_search_results(saved_id: str):
        try:
            return jsonify(items=service.results(saved_id))
        except KeyError as exc:
            raise NotFoundError("Gespeicherte Suche nicht gefunden") from exc

    @app.post("/api/saved-searches/<saved_id>/results/<result_key>/open")
    def api_saved_search_result_open(saved_id: str, result_key: str):
        try:
            return jsonify(result=service.materialize_result(saved_id, result_key))
        except KeyError as exc:
            raise NotFoundError("Gespeichertes Suchergebnis nicht gefunden") from exc
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    @app.post("/api/saved-searches/acknowledge")
    def api_saved_search_acknowledge():
        return jsonify(items=service.acknowledge_all())

    return scheduler
