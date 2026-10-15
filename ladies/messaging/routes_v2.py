from __future__ import annotations

import base64
import io
import json
import logging
import mimetypes
import queue
import uuid
from pathlib import Path

import qrcode
from flask import Response, jsonify, request, send_file, stream_with_context
from werkzeug.utils import secure_filename

from ladies_v9.database import Database
from ladies_v9.errors import ConflictError, NotFoundError, ValidationError
from ladies_v9.models import Person
from ladies_v9.request_parsing import read_bool, read_int

from .models import ChatMessage, ChatThread, ContactEndpoint
from .scope import bind_provider_address, refresh_endpoint_identity
from .service_v2 import MessagingService

LOGGER = logging.getLogger("ladies_explorer.messaging.routes")
MAX_MEDIA_BYTES = 100 * 1024 * 1024
MAX_MEDIA_REQUEST_BYTES = MAX_MEDIA_BYTES + (2 * 1024 * 1024)
ALLOWED_MEDIA_TYPES = {"IMAGE", "VIDEO", "AUDIO", "VOICE", "DOCUMENT"}
VIEW_ONCE_MEDIA_TYPES = {"IMAGE", "VIDEO", "AUDIO", "VOICE"}


def _qr_data_uri(value: str) -> str:
    image = qrcode.make(value)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _extension(file_name: str, mime_type: str) -> str:
    safe = secure_filename(file_name or "")
    suffix = Path(safe).suffix.lower()
    if suffix and len(suffix) <= 12:
        return suffix
    guessed = mimetypes.guess_extension((mime_type or "").split(";", 1)[0].strip())
    return (guessed or ".bin")[:12]


def install_messaging(app, *, data_dir, database: Database | None = None) -> MessagingService:
    database = database or app.extensions.get("ladies_database")
    if not isinstance(database, Database):
        raise RuntimeError("Messaging benötigt die gemeinsame Application-Database")
    service = MessagingService(database, data_dir)
    app.extensions["ladies_messaging"] = service

    # Messaging resources are authorized by their own persistence, not by the
    # current discovery/listing/preference state of an associated Person.
    def require_person(person_id: int) -> None:
        with database.session() as db:
            if db.get(Person, int(person_id)) is None:
                raise NotFoundError("Person nicht gefunden")

    def require_thread(thread_id: int) -> None:
        with database.session() as db:
            if db.get(ChatThread, int(thread_id)) is None:
                raise NotFoundError("Chat nicht gefunden")

    def require_message(message_id: int, *, thread_id: int | None = None) -> None:
        with database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None or (thread_id is not None and int(message.thread_id) != int(thread_id)):
                raise NotFoundError("Nachricht nicht gefunden")

    def require_endpoint(endpoint_id: int) -> None:
        with database.session() as db:
            if db.get(ContactEndpoint, int(endpoint_id)) is None:
                raise NotFoundError("Endpoint nicht gefunden")

    configured_limit = app.config.get("MAX_CONTENT_LENGTH")
    if configured_limit is None or int(configured_limit) > MAX_MEDIA_REQUEST_BYTES:
        app.config["MAX_CONTENT_LENGTH"] = MAX_MEDIA_REQUEST_BYTES

    @app.before_request
    def block_legacy_whatsapp_archive():
        if request.path.startswith("/archive/whatsapp/"):
            return jsonify(error="WhatsApp-Medien werden nicht direkt aus dem Archiv ausgeliefert."), 404
        return None

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
            raise NotFoundError("Kein QR-Code verfügbar")
        return jsonify(qr=_qr_data_uri(value))

    @app.get("/api/messaging/threads")
    def messaging_threads():
        filter_name = str(request.args.get("filter") or "ALL").upper()
        if filter_name not in {"ALL", "UNREAD", "UNASSIGNED"}:
            raise ValidationError("Ungültiger Filter")
        try:
            return jsonify(items=service.threads(filter_name))
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    @app.get("/api/messaging/threads/<int:thread_id>")
    def messaging_thread(thread_id: int):
        require_thread(thread_id)
        try:
            limit = read_int(request.args, "limit", default=100, minimum=1, maximum=200)
            before = read_int(request.args, "before", minimum=1) if request.args.get("before") else None
            return jsonify(service.thread(thread_id, limit, before))
        except KeyError as exc:
            raise NotFoundError("Chat oder Cursor nicht gefunden") from exc

    @app.post("/api/messaging/person/<int:person_id>/thread")
    def messaging_person_thread(person_id: int):
        require_person(person_id)
        data = request.get_json(silent=True) or {}
        try:
            listing_id = read_int(data, "listing_id", minimum=1) if data.get("listing_id") else None
            payload = service.ensure_person_thread(person_id, listing_id)
            endpoint_id = int((payload.get("endpoint") or {}).get("endpoint_id") or 0)
            if endpoint_id:
                refresh_endpoint_identity(service, endpoint_id)
            return jsonify(payload)
        except KeyError as exc:
            raise NotFoundError("Person nicht gefunden") from exc

    @app.get("/api/messaging/person/<int:person_id>")
    def messaging_person_summary(person_id: int):
        require_person(person_id)
        try:
            listing_id = read_int(request.args, "listing_id", minimum=1) if request.args.get("listing_id") else None
            return jsonify(service.person_summary(person_id, listing_id))
        except KeyError as exc:
            raise NotFoundError("Person nicht gefunden") from exc

    @app.get("/api/messaging/endpoints/<int:endpoint_id>/probe")
    def messaging_endpoint_probe(endpoint_id: int):
        require_endpoint(endpoint_id)
        with service.database.session() as db:
            endpoint = db.get(ContactEndpoint, endpoint_id)
            if endpoint is None:
                raise NotFoundError("Endpoint nicht gefunden")
            base = {
                "endpoint_id": endpoint.endpoint_id,
                "display_phone": endpoint.display_phone,
                "normalized_phone": endpoint.normalized_phone,
                "exists": None,
                "profile_picture_url": None,
                "detail": "not_connected",
            }
            if endpoint.state != "ACTIVE":
                base["detail"] = "identity_conflict"
                return jsonify(base)
            normalized_phone = endpoint.normalized_phone
        if service.gateway.state != "CONNECTED":
            return jsonify(base)
        if not normalized_phone:
            base["detail"] = "phone_unknown"
            return jsonify(base)
        jid = normalized_phone.lstrip("+") + "@s.whatsapp.net"
        try:
            event = service.gateway.command("contact_info", jid=jid, include_profile_picture=True, timeout=12)
            payload = event.get("payload") or {}
            base.update(
                {
                    "exists": payload.get("exists"),
                    "profile_picture_url": payload.get("profile_picture_url"),
                    "detail": payload.get("detail") or "unknown",
                }
            )
            if payload.get("exists") is True:
                bind_provider_address(service, endpoint_id, payload.get("jid"))
            return jsonify(base)
        except Exception:
            LOGGER.warning("WhatsApp contact probe failed endpoint_id=%s", endpoint_id, exc_info=True)
            base["detail"] = "probe_failed"
            return jsonify(base)

    @app.post("/api/messaging/threads/<int:thread_id>/messages")
    def messaging_send(thread_id: int):
        require_thread(thread_id)
        data = request.get_json(silent=True) or {}
        endpoint_id = read_int(data, "endpoint_id", minimum=1) if data.get("endpoint_id") else None
        if endpoint_id:
            require_endpoint(endpoint_id)
            refresh_endpoint_identity(service, endpoint_id)
        try:
            message = service.send_text(
                thread_id,
                data.get("text") or "",
                endpoint_id,
                read_int(data, "reply_to_message_id", minimum=1) if data.get("reply_to_message_id") else None,
            )
            sent_endpoint_id = int(message.get("endpoint_id") or endpoint_id or 0)
            if sent_endpoint_id:
                refresh_endpoint_identity(service, sent_endpoint_id)
            return jsonify(message=message)
        except KeyError as exc:
            raise NotFoundError("Chat nicht gefunden") from exc
        except (ValueError, RuntimeError) as exc:
            raise ValidationError(str(exc)) from exc

    @app.post("/api/messaging/threads/<int:thread_id>/internal")
    def messaging_internal(thread_id: int):
        require_thread(thread_id)
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(
                message=service.add_internal_message(
                    thread_id,
                    data.get("text") or "",
                    message_type=data.get("message_type") or "INTERNAL",
                )
            )
        except KeyError as exc:
            raise NotFoundError("Chat nicht gefunden") from exc
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    @app.post("/api/messaging/threads/<int:thread_id>/media")
    def messaging_send_media(thread_id: int):
        require_thread(thread_id)
        if request.content_length and request.content_length > MAX_MEDIA_REQUEST_BYTES:
            return jsonify(error="Datei ist zu groß (maximal 100 MB)"), 413
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise ValidationError("Datei fehlt")
        media_type = str(request.form.get("media_type") or "").upper()
        if media_type not in ALLOWED_MEDIA_TYPES:
            raise ValidationError("Ungültiger Medientyp")
        view_once = read_bool(request.form, "view_once", default=False)
        if view_once and media_type not in VIEW_ONCE_MEDIA_TYPES:
            raise ValidationError("Einmal ansehen ist für diesen Medientyp nicht verfügbar")
        endpoint_id = read_int(request.form, "endpoint_id", minimum=1) if request.form.get("endpoint_id") else None
        if endpoint_id:
            require_endpoint(endpoint_id)
            refresh_endpoint_identity(service, endpoint_id)
        reply_to = read_int(request.form, "reply_to_message_id", minimum=1) if request.form.get("reply_to_message_id") else None
        if reply_to:
            require_message(reply_to, thread_id=thread_id)
        mime_type = str(upload.mimetype or "application/octet-stream")
        original_name = secure_filename(upload.filename) or f"upload{_extension(upload.filename, mime_type)}"
        archive_name = f"upload-{uuid.uuid4().hex}{_extension(original_name, mime_type)}"
        target = service.media_store.upload_target(archive_name)
        try:
            upload.save(target)
            if target.stat().st_size > MAX_MEDIA_BYTES:
                target.unlink(missing_ok=True)
                return jsonify(error="Datei ist zu groß (maximal 100 MB)"), 413
            message = service.send_media(
                thread_id,
                archive_name=archive_name,
                media_type=media_type,
                mime_type=mime_type,
                file_name=original_name,
                caption=request.form.get("caption"),
                endpoint_id=endpoint_id,
                reply_to=reply_to,
                view_once=view_once,
            )
            sent_endpoint_id = int(message.get("endpoint_id") or endpoint_id or 0)
            if sent_endpoint_id:
                refresh_endpoint_identity(service, sent_endpoint_id)
            return jsonify(message=message)
        except KeyError as exc:
            target.unlink(missing_ok=True)
            raise NotFoundError("Chat nicht gefunden") from exc
        except (TypeError, ValueError) as exc:
            target.unlink(missing_ok=True)
            raise ValidationError(str(exc)) from exc
        except RuntimeError as exc:
            raise ValidationError(str(exc)) from exc

    @app.post("/api/messaging/messages/<int:message_id>/retry")
    def messaging_retry(message_id: int):
        require_message(message_id)
        try:
            return jsonify(message=service.retry(message_id))
        except KeyError as exc:
            raise NotFoundError("Nachricht nicht gefunden") from exc
        except (ValueError, RuntimeError) as exc:
            raise ValidationError(str(exc)) from exc

    @app.post("/api/messaging/messages/<int:message_id>/reaction")
    def messaging_reaction(message_id: int):
        require_message(message_id)
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(message=service.send_reaction(message_id, data.get("emoji") or ""))
        except KeyError as exc:
            raise NotFoundError("Nachricht nicht gefunden") from exc
        except (ValueError, RuntimeError) as exc:
            raise ValidationError(str(exc)) from exc

    @app.patch("/api/messaging/messages/<int:message_id>")
    def messaging_edit(message_id: int):
        require_message(message_id)
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(message=service.edit_message(message_id, data.get("text") or ""))
        except KeyError as exc:
            raise NotFoundError("Nachricht nicht gefunden") from exc
        except (ValueError, RuntimeError) as exc:
            raise ValidationError(str(exc)) from exc

    @app.delete("/api/messaging/messages/<int:message_id>")
    def messaging_delete(message_id: int):
        require_message(message_id)
        try:
            return jsonify(message=service.delete_message(message_id))
        except KeyError as exc:
            raise NotFoundError("Nachricht nicht gefunden") from exc
        except (ValueError, RuntimeError) as exc:
            raise ValidationError(str(exc)) from exc

    @app.get("/api/messaging/messages/<int:message_id>/media")
    def messaging_media(message_id: int):
        require_message(message_id)
        try:
            target, mime_type, file_name, as_attachment = service.media_path(message_id)
            response = send_file(
                target,
                mimetype=mime_type,
                as_attachment=as_attachment,
                download_name=file_name,
                conditional=False,
                max_age=0,
            )
            response.headers["Cache-Control"] = "private, no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["X-Content-Type-Options"] = "nosniff"
            if as_attachment:
                response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
            return response
        except KeyError as exc:
            raise NotFoundError("Nachricht nicht gefunden") from exc
        except FileNotFoundError:
            return jsonify(error="Medium ist lokal nicht mehr verfügbar"), 410
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    @app.get("/api/messaging/messages/<int:message_id>/view-once")
    def messaging_view_once(message_id: int):
        require_message(message_id)
        try:
            target, mime_type = service.view_once_path(message_id)
            response = send_file(target, mimetype=mime_type, conditional=False, max_age=0)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except KeyError as exc:
            raise NotFoundError("Nachricht nicht gefunden") from exc
        except FileNotFoundError:
            return jsonify(error="Einmal-Medium ist lokal nicht mehr verfügbar"), 410
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 410
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    @app.post("/api/messaging/threads/<int:thread_id>/presence")
    def messaging_presence(thread_id: int):
        require_thread(thread_id)
        data = request.get_json(silent=True) or {}
        endpoint_id = read_int(data, "endpoint_id", minimum=1) if data.get("endpoint_id") else None
        if endpoint_id:
            require_endpoint(endpoint_id)
        try:
            return jsonify(service.presence(thread_id, str(data.get("state") or "paused"), endpoint_id))
        except KeyError as exc:
            raise NotFoundError("Chat nicht gefunden") from exc
        except (ValueError, RuntimeError) as exc:
            raise ValidationError(str(exc)) from exc

    @app.post("/api/messaging/threads/<int:thread_id>/read")
    def messaging_read(thread_id: int):
        require_thread(thread_id)
        data = request.get_json(silent=True) or {}
        message_ids = data.get("message_ids")
        if message_ids is not None and not isinstance(message_ids, list):
            raise ValidationError("message_ids muss eine Liste sein")
        if message_ids is not None:
            for message_id in message_ids:
                require_message(int(message_id), thread_id=thread_id)
        try:
            return jsonify(service.mark_read(thread_id, message_ids))
        except KeyError as exc:
            raise NotFoundError("Chat nicht gefunden") from exc

    @app.get("/api/messaging/unassigned")
    def messaging_unassigned():
        return jsonify(items=service.threads("UNASSIGNED"))

    @app.get("/api/messaging/endpoints/<int:endpoint_id>/candidates")
    def messaging_candidates(endpoint_id: int):
        require_endpoint(endpoint_id)
        try:
            return jsonify(items=service.assignment_candidates(endpoint_id))
        except KeyError as exc:
            raise NotFoundError("Endpoint nicht gefunden") from exc

    @app.post("/api/messaging/endpoints/<int:endpoint_id>/assign")
    def messaging_assign(endpoint_id: int):
        require_endpoint(endpoint_id)
        data = request.get_json(silent=True) or {}
        try:
            person_id = read_int(data, "person_id", minimum=1)
            require_person(person_id)
            return jsonify(endpoint=service.assign_endpoint(endpoint_id, person_id), person_id=person_id)
        except KeyError as exc:
            raise NotFoundError("Endpoint oder Person nicht gefunden") from exc
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc

    @app.post("/api/messaging/settings")
    def messaging_settings():
        data = request.get_json(silent=True) or {}
        if "typing_enabled" in data:
            service.set_typing_enabled(read_bool(data, "typing_enabled"))
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


__all__ = ["install_messaging"]
