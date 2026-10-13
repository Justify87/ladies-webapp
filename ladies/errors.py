from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge


@dataclass(slots=True)
class AppError(Exception):
    message: str
    status_code: int = 400
    code: str = "APP_ERROR"
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


class ValidationError(AppError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message, 400, "VALIDATION_ERROR", details)


class NotFoundError(AppError):
    def __init__(self, message: str):
        super().__init__(message, 404, "NOT_FOUND")


class ConflictError(AppError):
    def __init__(self, message: str):
        super().__init__(message, 409, "CONFLICT")


class ExternalServiceError(AppError):
    def __init__(self, message: str = "Externer Dienst konnte nicht erreicht werden."):
        super().__init__(message, 502, "EXTERNAL_SERVICE_ERROR")


def error_payload(error: AppError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error": error.message,
        "code": error.code,
        "request_id": getattr(g, "request_id", None),
    }
    if error.details:
        payload["details"] = error.details
    return payload


def install_error_handlers(app: Flask) -> None:
    @app.errorhandler(AppError)
    def handle_app_error(error: AppError):
        return jsonify(error_payload(error)), error.status_code

    @app.errorhandler(RequestEntityTooLarge)
    def handle_payload_too_large(error: RequestEntityTooLarge):
        if request.path.startswith("/api/"):
            return jsonify({
                "error": "Anfrage ist zu groß",
                "code": "PAYLOAD_TOO_LARGE",
                "request_id": getattr(g, "request_id", None),
            }), 413
        return error

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):
        if isinstance(error, HTTPException):
            return error
        app.logger.exception(
            "Unhandled request error id=%s path=%s",
            getattr(g, "request_id", "-"),
            request.path,
            exc_info=error,
        )
        if request.path.startswith("/api/"):
            return jsonify({
                "error": "Interner Serverfehler",
                "code": "INTERNAL_ERROR",
                "request_id": getattr(g, "request_id", None),
            }), 500
        return "Interner Serverfehler", 500