from __future__ import annotations

from flask import jsonify, request

from .service_hardened import PriceIntelligenceService


def install_pricing(app, *, db_path, data_dir) -> PriceIntelligenceService:
    service = PriceIntelligenceService(db_path, data_dir)
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
        except KeyError:
            return jsonify(error="Chat nicht gefunden"), 404

    @app.post("/api/pricing/threads/<int:thread_id>/proposals")
    def pricing_create_proposal(thread_id: int):
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(service.create_proposal(
                thread_id,
                trigger_type=str(data.get("trigger_type") or "MANUAL"),
                seed_message_id=int(data["seed_message_id"]) if data.get("seed_message_id") else None,
            ))
        except KeyError:
            return jsonify(error="Chat nicht gefunden"), 404
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.get("/api/pricing/proposals/<int:extraction_id>")
    def pricing_get_proposal(extraction_id: int):
        try:
            return jsonify(service.proposal(extraction_id))
        except KeyError:
            return jsonify(error="Preisvorschlag nicht gefunden"), 404

    @app.post("/api/pricing/proposals/<int:extraction_id>/accept")
    def pricing_accept_proposal(extraction_id: int):
        try:
            result = service.accept(extraction_id, request.get_json(silent=True) or {})
            if result.get("status") == "STALE":
                return jsonify(result), 409
            return jsonify(result)
        except KeyError:
            return jsonify(error="Preisvorschlag nicht gefunden"), 404
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.post("/api/pricing/proposals/<int:extraction_id>/reject")
    def pricing_reject_proposal(extraction_id: int):
        try:
            return jsonify(service.reject(extraction_id))
        except KeyError:
            return jsonify(error="Preisvorschlag nicht gefunden"), 404
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @app.get("/api/pricing/person/<int:person_id>/current")
    def pricing_current(person_id: int):
        try:
            return jsonify(service.current(person_id))
        except KeyError:
            return jsonify(error="Person nicht gefunden"), 404

    @app.get("/api/pricing/person/<int:person_id>/history")
    def pricing_history(person_id: int):
        try:
            limit = int(request.args.get("limit") or 30)
            return jsonify(items=service.history(person_id, limit))
        except KeyError:
            return jsonify(error="Person nicht gefunden"), 404
        except ValueError:
            return jsonify(error="Ungültiges Limit"), 400

    return service
