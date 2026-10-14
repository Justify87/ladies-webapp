from __future__ import annotations

from flask import jsonify, request

from ladies_v9.database import Database
from ladies_v9.errors import ConflictError, NotFoundError, ValidationError
from ladies_v9.request_parsing import read_int

from .revision import create_revision
from .service import PriceIntelligenceService


def install_pricing(app, *, data_dir, database: Database | None = None) -> PriceIntelligenceService:
    database = database or app.extensions.get("ladies_database")
    if not isinstance(database, Database):
        raise RuntimeError("Pricing benötigt die gemeinsame Application-Database")
    service = PriceIntelligenceService(database, data_dir)
    app.extensions["ladies_pricing"] = service

    @app.get("/api/pricing/runtime/status")
    def pricing_runtime_status():
        return jsonify(service.runtime_status())

    @app.route("/api/pricing/settings", methods=["GET", "POST"])
    def pricing_settings():
        if request.method == "POST":
            return jsonify(settings=service.set_settings(request.get_json(silent=True) or {}))
        return jsonify(settings=service.settings())

    @app.get("/api/pricing/threads/<int:thread_id>/signal")
    def pricing_signal(thread_id: int):
        try:
            return jsonify(service.signal(thread_id))
        except KeyError as exc:
            raise NotFoundError("Chat nicht gefunden") from exc

    @app.get("/api/pricing/threads/<int:thread_id>/timeline")
    def pricing_timeline(thread_id: int):
        try:
            limit = read_int(request.args, "limit", default=30, minimum=1, maximum=100)
            return jsonify(service.timeline(thread_id, limit=limit))
        except KeyError as exc:
            raise NotFoundError("Chat nicht gefunden") from exc

    @app.post("/api/pricing/threads/<int:thread_id>/proposals")
    def pricing_create_proposal(thread_id: int):
        data = request.get_json(silent=True) or {}
        try:
            seed_message_id = read_int(data, "seed_message_id", minimum=1) if data.get("seed_message_id") else None
            return jsonify(service.create_proposal(
                thread_id,
                trigger_type=str(data.get("trigger_type") or "MANUAL"),
                seed_message_id=seed_message_id,
            ))
        except KeyError as exc:
            raise NotFoundError("Chat nicht gefunden") from exc
        except (ValueError, TypeError) as exc:
            raise ValidationError(str(exc)) from exc

    @app.get("/api/pricing/proposals/<int:extraction_id>")
    def pricing_get_proposal(extraction_id: int):
        try:
            return jsonify(service.proposal(extraction_id))
        except KeyError as exc:
            raise NotFoundError("Preisvorschlag nicht gefunden") from exc

    @app.post("/api/pricing/proposals/<int:extraction_id>/accept")
    def pricing_accept_proposal(extraction_id: int):
        try:
            result = service.accept(extraction_id, request.get_json(silent=True) or {})
            if result.get("status") == "STALE":
                raise ConflictError(result.get("error") or "Preisvorschlag ist veraltet")
            return jsonify(result)
        except KeyError as exc:
            raise NotFoundError("Preisvorschlag nicht gefunden") from exc
        except (ValueError, TypeError) as exc:
            raise ValidationError(str(exc)) from exc

    @app.post("/api/pricing/proposals/<int:extraction_id>/revise")
    def pricing_revise_proposal(extraction_id: int):
        try:
            return jsonify(create_revision(service, extraction_id))
        except KeyError as exc:
            raise NotFoundError("Preisvorschlag nicht gefunden") from exc
        except (ValueError, TypeError) as exc:
            raise ValidationError(str(exc)) from exc

    @app.post("/api/pricing/proposals/<int:extraction_id>/reject")
    def pricing_reject_proposal(extraction_id: int):
        try:
            return jsonify(service.reject(extraction_id))
        except KeyError as exc:
            raise NotFoundError("Preisvorschlag nicht gefunden") from exc
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    @app.get("/api/pricing/person/<int:person_id>/current")
    def pricing_current(person_id: int):
        try:
            return jsonify(service.current(person_id))
        except KeyError as exc:
            raise NotFoundError("Person nicht gefunden") from exc

    @app.get("/api/pricing/person/<int:person_id>/history")
    def pricing_history(person_id: int):
        try:
            limit = read_int(request.args, "limit", default=30, minimum=1, maximum=100)
            return jsonify(items=service.history(person_id, limit))
        except KeyError as exc:
            raise NotFoundError("Person nicht gefunden") from exc

    return service
