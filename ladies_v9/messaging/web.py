from __future__ import annotations

import base64
import io
import json
import queue

import qrcode
from flask import Response, jsonify, request, stream_with_context

from ladies_v9.models import Person, PersonUserState
from .models import ContactEndpoint
from .service_v92 import MessagingService


def _qr_data_uri(value: str) -> str:
    image = qrcode.make(value)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def install_messaging(app, *, db_path, data_dir) -> MessagingService:
    service = MessagingService(db_path, data_dir)
    app.extensions["ladies_messaging"] = service

    @app.get("/api/messaging/status")
    def messaging_status():
        return jsonify(service.status())

    @app.post("/api/messaging/connect")
    def messaging_connect():
        return jsonify(service.connect())

    @app.post("/api/messaging/disconnect")
    def messaging_disconnect():
        return jsonify(service.disconnect())

    @app.post("/api/messaging/logout")
    def messaging_logout():
        return jsonify(service.logout())

    @app.get("/api/messaging/qr")
    def messaging_qr():
        value = service.qr()
        if not value:
            return jsonify(qr=None), 404
        return jsonify(qr=_qr_data_uri(value))

    @app.get("/api/messaging/threads")
    def messaging_threads():
        filter_name = str(request.args.get("filter") or "ALL").upper()
        if filter_name not in {"ALL", "UNREAD", "UNASSIGNED"}:
            return jsonify(error="Ungültiger Filter"), 400
        return jsonify(items=service.threads(filter_name))

    @app.get("/api/messaging/threads/<int:thread_id>")
    def messaging_thread(thread_id: int):
        try:
            limit = int(request.args.get("limit") or 100)
            before = int(request.args["before"]) if request.args.get("before") else None
            return jsonify(service.thread(thread_id, limit, before))
        except (TypeError, ValueError):
            return jsonify(error="Ungültige Pagination"), 400
        except KeyError:
            return jsonify(error="Chat oder Cursor nicht gefunden"), 404

    @app.post("/api/messaging/person/<int:person_id>/thread")
    def messaging_person_thread(person_id: int):
        data = request.get_json(silent=True) or {}
        try:
            listing_id = int(data["listing_id"]) if data.get("listing_id") else None
            return jsonify(service.ensure_person_thread(person_id, listing_id))
        except KeyError:
            return jsonify(error="Person nicht gefunden"), 404

    @app.get("/api/messaging/person/<int:person_id>")
    def messaging_person_summary(person_id: int):
        try:
            listing_id = int(request.args.get("listing_id")) if request.args.get("listing_id") else None
            return jsonify(service.person_summary(person_id, listing_id))
        except KeyError:
            return jsonify(error="Person nicht gefunden"), 404

    @app.post("/api/messaging/threads/<int:thread_id>/messages")
    def messaging_send(thread_id: int):
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(message=service.send_text(
                thread_id,
                data.get("text") or "",
                int(data["endpoint_id"]) if data.get("endpoint_id") else None,
                int(data["reply_to_message_id"]) if data.get("reply_to_message_id") else None,
            ))
        except KeyError:
            return jsonify(error="Chat nicht gefunden"), 404
        except (ValueError, RuntimeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.post("/api/messaging/messages/<int:message_id>/retry")
    def messaging_retry(message_id: int):
        try:
            return jsonify(message=service.retry(message_id))
        except KeyError:
            return jsonify(error="Nachricht nicht gefunden"), 404
        except (ValueError, RuntimeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.post("/api/messaging/threads/<int:thread_id>/presence")
    def messaging_presence(thread_id: int):
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(service.presence(
                thread_id,
                str(data.get("state") or "paused"),
                int(data["endpoint_id"]) if data.get("endpoint_id") else None,
            ))
        except (ValueError, RuntimeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.post("/api/messaging/threads/<int:thread_id>/read")
    def messaging_read(thread_id: int):
        try:
            return jsonify(service.mark_read(thread_id))
        except KeyError:
            return jsonify(error="Chat nicht gefunden"), 404

    @app.get("/api/messaging/unassigned")
    def messaging_unassigned():
        return jsonify(items=service.unassigned())

    @app.get("/api/messaging/endpoints/<int:endpoint_id>/candidates")
    def messaging_candidates(endpoint_id: int):
        try:
            return jsonify(items=service.assignment_candidates(endpoint_id))
        except KeyError:
            return jsonify(error="Endpoint nicht gefunden"), 404

    @app.post("/api/messaging/endpoints/<int:endpoint_id>/assign")
    def messaging_assign(endpoint_id: int):
        data = request.get_json(silent=True) or {}
        try:
            person_id = data.get("person_id")
            if data.get("create_person"):
                display_name = str(data.get("display_name") or "WhatsApp-Kontakt")[:160]
                with service.database.session() as db:
                    endpoint = db.get(ContactEndpoint, endpoint_id)
                    if endpoint is None:
                        raise KeyError(endpoint_id)
                    person = Person(display_name=display_name)
                    db.add(person)
                    db.flush()
                    db.add(PersonUserState(person_id=person.person_id))
                    person_id = person.person_id
            if not person_id:
                return jsonify(error="person_id fehlt"), 400
            return jsonify(endpoint=service.assign_endpoint(endpoint_id, int(person_id)), person_id=int(person_id))
        except KeyError:
            return jsonify(error="Endpoint oder Person nicht gefunden"), 404
        except ValueError as exc:
            return jsonify(error=str(exc)), 409

    @app.post("/api/messaging/settings")
    def messaging_settings():
        data = request.get_json(silent=True) or {}
        if "typing_enabled" in data:
            service.set_typing_enabled(bool(data["typing_enabled"]))
        return jsonify(service.status())

    @app.get("/api/messaging/events")
    def messaging_events():
        subscriber = service.events.subscribe()

        @stream_with_context
        def generate():
            try:
                yield "retry: 2000\n\n"
                while True:
                    try:
                        event = subscriber.get(timeout=20)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except queue.Empty:
                        yield ": keepalive\n\n"
            finally:
                service.events.unsubscribe(subscriber)

        response = Response(generate(), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    return service
