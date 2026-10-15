from __future__ import annotations

from flask import jsonify, request

from ladies_v9.errors import NotFoundError, ValidationError

from .classification import LADIES_CONFIRMED, NON_LADIES, UNKNOWN, MessagingClassifier
from .routes_v2 import install_messaging as install_core_messaging


def install_messaging(app, *, data_dir, database=None):
    """Install canonical messaging plus a persistent product-classification layer."""

    service = install_core_messaging(app, data_dir=data_dir, database=database)
    classifier = MessagingClassifier(service.database)
    app.extensions["ladies_messaging_classifier"] = classifier

    @app.get("/api/messaging/inbox")
    def messaging_inbox():
        filter_name = str(request.args.get("filter") or "ALL").upper()
        if filter_name not in {"ALL", "UNREAD"}:
            raise ValidationError("Ungültiger Nachrichtenfilter")
        items = classifier.filter_items(service.threads("ALL"), view="INBOX")
        if filter_name == "UNREAD":
            items = [item for item in items if int(item.get("unread_count") or 0) > 0]
        return jsonify(items=items, classification=classifier.summary())

    @app.get("/api/messaging/review")
    def messaging_review_queue():
        items = classifier.filter_items(service.threads("ALL"), view="REVIEW")
        return jsonify(items=items, classification=classifier.summary())

    @app.get("/api/messaging/classification")
    def messaging_classification_summary():
        return jsonify(classifier.summary())

    @app.post("/api/messaging/threads/<int:thread_id>/classification")
    def messaging_classify_thread(thread_id: int):
        data = request.get_json(silent=True) or {}
        status = str(data.get("status") or "").upper()
        method = str(data.get("method") or "manual")
        if status not in {LADIES_CONFIRMED, UNKNOWN, NON_LADIES}:
            raise ValidationError("Ungültige Chat-Klassifizierung")
        try:
            return jsonify(classifier.set_thread(thread_id, status, method=method))
        except KeyError as exc:
            raise NotFoundError("Chat nicht gefunden") from exc
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    return service


__all__ = ["install_messaging"]
