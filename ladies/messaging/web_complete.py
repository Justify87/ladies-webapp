from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from flask import jsonify, request, send_file
from werkzeug.utils import secure_filename

from .web import install_messaging as install_base_messaging

MAX_MEDIA_BYTES = 100 * 1024 * 1024
ALLOWED_MEDIA_TYPES = {"IMAGE", "VIDEO", "AUDIO", "VOICE", "DOCUMENT"}


def _bool(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _int(value):
    if value in {None, ""}:
        return None
    return int(value)


def _extension(file_name: str, mime_type: str) -> str:
    safe = secure_filename(file_name or "")
    suffix = Path(safe).suffix.lower()
    if suffix and len(suffix) <= 12:
        return suffix
    guessed = mimetypes.guess_extension((mime_type or "").split(";", 1)[0].strip())
    return (guessed or ".bin")[:12]


def install_messaging(app, *, db_path, data_dir):
    service = install_base_messaging(app, db_path=db_path, data_dir=data_dir)

    @app.post("/api/messaging/threads/<int:thread_id>/media")
    def messaging_send_media(thread_id: int):
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify(error="Datei fehlt"), 400
        media_type = str(request.form.get("media_type") or "").upper()
        if media_type not in ALLOWED_MEDIA_TYPES:
            return jsonify(error="Ungültiger Medientyp"), 400
        if request.content_length and request.content_length > MAX_MEDIA_BYTES + (2 * 1024 * 1024):
            return jsonify(error="Datei ist zu groß (maximal 100 MB)"), 413

        mime_type = str(upload.mimetype or "application/octet-stream")
        original_name = secure_filename(upload.filename) or f"upload{_extension(upload.filename, mime_type)}"
        archive_name = f"upload-{uuid.uuid4().hex}{_extension(original_name, mime_type)}"
        target = service.media_dir / archive_name
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
                endpoint_id=_int(request.form.get("endpoint_id")),
                reply_to=_int(request.form.get("reply_to_message_id")),
                view_once=_bool(request.form.get("view_once")),
            )
            return jsonify(message=message)
        except KeyError:
            target.unlink(missing_ok=True)
            return jsonify(error="Chat nicht gefunden"), 404
        except (TypeError, ValueError, RuntimeError) as exc:
            # Keep the file when a ChatMessage was already created: retry depends on it.
            return jsonify(error=str(exc)), 400

    @app.post("/api/messaging/messages/<int:message_id>/reaction")
    def messaging_reaction(message_id: int):
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(message=service.send_reaction(message_id, data.get("emoji") or ""))
        except KeyError:
            return jsonify(error="Nachricht nicht gefunden"), 404
        except (ValueError, RuntimeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.patch("/api/messaging/messages/<int:message_id>")
    def messaging_edit(message_id: int):
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(message=service.edit_message(message_id, data.get("text") or ""))
        except KeyError:
            return jsonify(error="Nachricht nicht gefunden"), 404
        except (ValueError, RuntimeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.delete("/api/messaging/messages/<int:message_id>")
    def messaging_delete(message_id: int):
        try:
            return jsonify(message=service.delete_message(message_id))
        except KeyError:
            return jsonify(error="Nachricht nicht gefunden"), 404
        except (ValueError, RuntimeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.get("/api/messaging/messages/<int:message_id>/view-once")
    def messaging_view_once(message_id: int):
        try:
            target, mime_type = service.view_once_path(message_id)
            response = send_file(target, mimetype=mime_type, conditional=False, max_age=0)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        except KeyError:
            return jsonify(error="Nachricht nicht gefunden"), 404
        except FileNotFoundError:
            return jsonify(error="Einmal-Medium ist lokal nicht mehr verfügbar"), 410
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 410
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    return service
