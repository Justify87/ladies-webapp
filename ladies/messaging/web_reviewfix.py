from __future__ import annotations

from flask import jsonify, request, send_file

from .web_complete import install_messaging as install_complete_messaging


def install_messaging(app, *, db_path, data_dir):
    service = install_complete_messaging(app, db_path=db_path, data_dir=data_dir)

    @app.before_request
    def block_legacy_whatsapp_archive():
        # WhatsApp media must go through message-aware endpoints so documents
        # cannot execute same-origin active content and view-once cannot bypass
        # its single-consumption gate.
        if request.path.startswith("/archive/whatsapp/"):
            return jsonify(error="WhatsApp-Medien werden nicht direkt aus dem Archiv ausgeliefert."), 404
        return None

    @app.get("/api/messaging/messages/<int:message_id>/media")
    def messaging_media(message_id: int):
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
        except KeyError:
            return jsonify(error="Nachricht nicht gefunden"), 404
        except FileNotFoundError:
            return jsonify(error="Medium ist lokal nicht mehr verfügbar"), 410
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    return service
