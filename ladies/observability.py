from __future__ import annotations

import atexit
from contextlib import contextmanager
import logging
import os
from typing import Any, Callable, TypeVar
from urllib.parse import urlsplit

LOGGER = logging.getLogger("ladies_explorer.observability")
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FLASK_EXCLUDED_URLS = (
    r"/static/.*," r"/archive/.*," r"/pair/.*," r"/api/messaging/events(?:\?.*)?$," r"/api/client-log(?:\?.*)?$"
)
_T = TypeVar("_T")

_STATE: dict[str, Any] = {
    "enabled": False,
    "provider": None,
    "tracer": None,
    "logging_instrumentor": None,
    "shutdown_registered": False,
}


class _TraceContextMessageFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        trace_id = str(getattr(record, "otelTraceID", "0") or "0")
        if trace_id == "0":
            return True
        span_id = str(getattr(record, "otelSpanID", "0") or "0")
        if getattr(record, "_ladies_trace_prefix", False):
            return True
        message = record.getMessage()
        record.msg = f"[trace={trace_id} span={span_id}] {message}"
        record.args = ()
        record._ladies_trace_prefix = True
        return True


def _enabled_from_env(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in _TRUE_VALUES


def enabled() -> bool:
    return _enabled_from_env("LADIES_OTEL_ENABLED")


def _set_server_url_attributes(span, *, path: str, clean_url: str) -> None:
    if span is None or not span.is_recording():
        return
    span.set_attribute("url.full", clean_url)
    span.set_attribute("http.url", clean_url)
    span.set_attribute("http.target", path)
    span.set_attribute("url.query", "")


def _server_request_hook(span, environ) -> None:
    path = str(environ.get("PATH_INFO") or "/")
    scheme = str(environ.get("wsgi.url_scheme") or "http")
    host = str(environ.get("HTTP_HOST") or environ.get("SERVER_NAME") or "localhost")
    _set_server_url_attributes(span, path=path, clean_url=f"{scheme}://{host}{path}")


def _server_response_hook(span, _status, _response_headers) -> None:
    if span is None or not span.is_recording():
        return
    from flask import request

    _set_server_url_attributes(span, path=request.path or "/", clean_url=request.base_url)


def _install_log_correlation() -> None:
    logger = logging.getLogger("ladies_explorer")
    for handler in logger.handlers:
        if any(isinstance(item, _TraceContextMessageFilter) for item in handler.filters):
            continue
        handler.addFilter(_TraceContextMessageFilter())


def _remove_log_correlation() -> None:
    logger = logging.getLogger("ladies_explorer")
    for handler in logger.handlers:
        for item in list(handler.filters):
            if isinstance(item, _TraceContextMessageFilter):
                handler.removeFilter(item)


def init_observability(app, engine, *, service_version: str, _span_exporter=None) -> None:
    if not enabled() or _STATE["enabled"]:
        return

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.flask import FlaskInstrumentor
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        LOGGER.exception("OpenTelemetry is enabled but dependencies are unavailable")
        return

    resource = Resource.create(
        {
            SERVICE_NAME: "ladies-explorer",
            SERVICE_VERSION: str(service_version or "unknown"),
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = _span_exporter if _span_exporter is not None else OTLPSpanExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    tracer = provider.get_tracer("ladies_explorer")

    FlaskInstrumentor().instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls=_FLASK_EXCLUDED_URLS,
        request_hook=_server_request_hook,
        response_hook=_server_response_hook,
        enable_commenter=False,
    )
    SQLAlchemyInstrumentor().instrument(
        engine=engine,
        tracer_provider=provider,
        enable_commenter=False,
        enable_attribute_commenter=False,
    )
    logging_instrumentor = LoggingInstrumentor()
    logging_instrumentor.instrument(
        tracer_provider=provider,
        inject_trace_context=True,
        set_logging_format=False,
        enable_log_auto_instrumentation=False,
    )
    _install_log_correlation()

    _STATE.update(
        enabled=True,
        provider=provider,
        tracer=tracer,
        logging_instrumentor=logging_instrumentor,
    )
    if not _STATE["shutdown_registered"]:
        atexit.register(shutdown_observability)
        _STATE["shutdown_registered"] = True
    LOGGER.info("OpenTelemetry tracing enabled service_version=%s", service_version)


def shutdown_observability() -> None:
    provider = _STATE.get("provider")
    logging_instrumentor = _STATE.get("logging_instrumentor")
    try:
        if provider is not None:
            provider.shutdown()
    finally:
        if logging_instrumentor is not None:
            try:
                logging_instrumentor.uninstrument()
            except Exception:
                LOGGER.debug("OpenTelemetry logging cleanup failed", exc_info=True)
        _remove_log_correlation()
        _STATE.update(enabled=False, provider=None, tracer=None, logging_instrumentor=None)


def current_trace_id() -> str | None:
    if not _STATE["enabled"]:
        return None
    try:
        from opentelemetry.trace import get_current_span
    except ImportError:
        return None
    context = get_current_span().get_span_context()
    if not getattr(context, "is_valid", False):
        return None
    return format(context.trace_id, "032x")


def capture_context():
    if not _STATE["enabled"]:
        return None
    try:
        from opentelemetry import context
    except ImportError:
        return None
    return context.get_current()


def _safe_attributes(attributes: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (attributes or {}).items():
        if value is None:
            continue
        safe_key = str(key)[:96]
        safe[safe_key] = str(value)[:160] if isinstance(value, str) else value
    return safe


@contextmanager
def operation_span(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
    parent_context=None,
    kind: str = "internal",
):
    tracer = _STATE.get("tracer")
    if not _STATE["enabled"] or tracer is None:
        yield None
        return

    from opentelemetry.trace import SpanKind, Status, StatusCode

    span_kind = SpanKind.CLIENT if kind == "client" else SpanKind.INTERNAL
    with tracer.start_as_current_span(
        str(name)[:96],
        context=parent_context,
        kind=span_kind,
        attributes=_safe_attributes(attributes),
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except Exception as exc:
            if span.is_recording():
                span.set_attribute("error.type", type(exc).__qualname__[:160])
                span.set_status(Status(StatusCode.ERROR))
            raise


def background_task_span(task_type: str, *, queue_wait_ms: float, parent_context=None):
    return operation_span(
        "background.task",
        parent_context=parent_context,
        attributes={
            "task.type": str(task_type or "background")[:64],
            "queue.wait_ms": max(0.0, float(queue_wait_ms)),
        },
    )


def traced_http_call(
    method: str,
    url: str,
    *,
    operation: str,
    call: Callable[[], _T],
) -> _T:
    parts = urlsplit(str(url or ""))
    attributes = {
        "http.request.method": str(method or "GET").upper()[:16],
        "http.operation": str(operation or "request")[:80],
        "server.address": str(parts.hostname or "unknown")[:160],
        "url.scheme": str(parts.scheme or "")[:16],
    }
    with operation_span("http.client", attributes=attributes, kind="client") as span:
        response = call()
        status_code = getattr(response, "status_code", None)
        if span is not None and status_code is not None:
            status = int(status_code)
            span.set_attribute("http.response.status_code", status)
            if status >= 400:
                from opentelemetry.trace import Status, StatusCode

                span.set_status(Status(StatusCode.ERROR))
        return response
