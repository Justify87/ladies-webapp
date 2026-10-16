#!/usr/bin/env python3
from __future__ import annotations

import io
import logging
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from flask import Flask, jsonify
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine, text

from ladies_v9 import observability
from ladies_v9.background import BackgroundTasks
from ladies_v9.profiling import install_request_profiling
from ladies_v9.source import http_get

ROOT = Path(__file__).resolve().parents[1]


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/fail"):
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args) -> None:
        return


def _span_values(spans) -> str:
    values: list[str] = []
    for span in spans:
        values.append(span.name)
        for key, value in (span.attributes or {}).items():
            values.extend((str(key), str(value)))
        status = getattr(span, "status", None)
        if status is not None:
            values.append(str(getattr(status, "description", "") or ""))
        for event in getattr(span, "events", ()) or ():
            values.append(str(event.name))
            for key, value in (event.attributes or {}).items():
                values.extend((str(key), str(value)))
    return "\n".join(values)


def check_disabled_is_noop() -> None:
    os.environ.pop("LADIES_OTEL_ENABLED", None)
    app = Flask("otel-disabled")
    engine = create_engine("sqlite:///:memory:")
    exporter = InMemorySpanExporter()
    observability.init_observability(app, engine, service_version="test", _span_exporter=exporter)
    assert observability._STATE["enabled"] is False
    assert not getattr(app, "_is_instrumented_by_opentelemetry", False)
    engine.dispose()


def check_targeted_boundaries() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    observability_source = (ROOT / "ladies/observability.py").read_text(encoding="utf-8")
    assert "opentelemetry-instrumentation-threading" not in pyproject
    assert "opentelemetry-instrumentation-requests" not in pyproject
    assert "ThreadingInstrumentor" not in observability_source
    assert "RequestsInstrumentor" not in observability_source

    gateway = (ROOT / "ladies/messaging/gateway.py").read_text(encoding="utf-8")
    pricing = (ROOT / "ladies/pricing/model_runtime.py").read_text(encoding="utf-8")
    source = (ROOT / "ladies/source.py").read_text(encoding="utf-8")
    map_service = (ROOT / "ladies/core/services/map_locations.py").read_text(encoding="utf-8")
    media = (ROOT / "ladies/media.py").read_text(encoding="utf-8")
    assert '"messaging.command"' in gateway
    assert '"pricing.model"' in pricing
    for content in (source, map_service, media):
        assert "traced_http_call" in content


def check_tracing() -> None:
    os.environ["LADIES_OTEL_ENABLED"] = "1"
    exporter = InMemorySpanExporter()
    app = Flask("otel-runtime")
    engine = create_engine("sqlite:///:memory:")
    background = BackgroundTasks(max_workers=1, max_pending=2)

    external = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    external_thread = threading.Thread(target=external.serve_forever, daemon=True)
    external_thread.start()
    base_url = f"http://127.0.0.1:{external.server_port}"
    session = requests.Session()

    log_stream = io.StringIO()
    app_logger = logging.getLogger("ladies_explorer")
    old_level = app_logger.level
    handler = logging.StreamHandler(log_stream)
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)

    @app.get("/api/test")
    def traced_request():
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
        logging.getLogger("ladies_explorer.observability_check").warning("observability-log")

        plain_thread_trace_ids: list[str | None] = []
        plain_thread = threading.Thread(
            target=lambda: plain_thread_trace_ids.append(observability.current_trace_id()),
            daemon=True,
        )
        plain_thread.start()
        plain_thread.join(timeout=2)
        assert plain_thread_trace_ids == [None], "Plain threads must not inherit request trace context"

        future = background.submit("wa-hydrate:123456", lambda: "done")
        assert future is not None
        assert future.result(timeout=3) == "done"

        response = http_get(
            session,
            f"{base_url}/external?token=outbound-secret",
            context="observability-success",
            headers={"Authorization": "Bearer header-secret"},
            timeout=3,
        )
        response.raise_for_status()
        try:
            http_get(
                session,
                f"{base_url}/fail?token=failed-secret",
                context="profile-123456",
                timeout=3,
            )
        except requests.RequestException:
            pass
        else:
            raise AssertionError("Expected failed HTTP request")
        return jsonify(ok=True)

    @app.get("/api/messaging/events")
    def excluded_events():
        return "events"

    @app.post("/api/client-log")
    def excluded_client_log():
        return jsonify(ok=True)

    observability.init_observability(app, engine, service_version="test", _span_exporter=exporter)
    try:
        assert not any(
            item.__class__.__module__.startswith("opentelemetry.sdk._logs")
            for item in logging.getLogger().handlers
        ), "Tracing must not install an OpenTelemetry log exporter/handler"

        client = app.test_client()
        response = client.get(
            "/api/test?token=inbound-secret",
            headers={"Authorization": "Bearer inbound-header-secret"},
        )
        assert response.status_code == 200
        assert response.get_json() == {"ok": True}

        provider = observability._STATE["provider"]
        assert provider is not None and provider.force_flush(timeout_millis=5000)
        spans = list(exporter.get_finished_spans())
        assert spans, "Tracing must export spans"

        server_span = next(
            span for span in spans if (span.attributes or {}).get("http.route") == "/api/test"
        )
        background_span = next(span for span in spans if span.name == "background.task")
        assert background_span.parent is not None
        assert background_span.parent.span_id == server_span.context.span_id
        assert background_span.attributes.get("task.type") == "wa-hydrate"
        assert "123456" not in _span_values([background_span])

        assert any(
            (span.attributes or {}).get("db.system.name") == "sqlite"
            or (span.attributes or {}).get("db.system") == "sqlite"
            or span.name.upper().startswith("SELECT")
            for span in spans
        ), "SQLAlchemy query span missing"

        http_spans = [span for span in spans if span.name == "http.client"]
        assert {str(span.attributes.get("http.operation")) for span in http_spans} >= {
            "observability-success",
            "profile",
        }
        assert all(str(span.kind.name) == "CLIENT" for span in http_spans)
        failed_span = next(
            span for span in http_spans if span.attributes.get("http.operation") == "profile"
        )
        assert failed_span.attributes.get("error.type"), "Failed HTTP span must expose only the error type"
        assert not any(event.name == "exception" for event in failed_span.events), (
            "Manual HTTP spans must not export exception.message or stacktrace events"
        )

        captured = _span_values(spans)
        for secret in (
            "inbound-secret",
            "outbound-secret",
            "failed-secret",
            "header-secret",
            "inbound-header-secret",
        ):
            assert secret not in captured, f"Sensitive value leaked into spans/events: {secret}"
        assert "observability-log" in log_stream.getvalue()
        assert "[trace=" in log_stream.getvalue() and " span=" in log_stream.getvalue()

        exporter.clear()
        assert client.get("/api/messaging/events").status_code == 200
        assert client.post("/api/client-log").status_code == 200
        assert provider.force_flush(timeout_millis=5000)
        assert not exporter.get_finished_spans(), "Excluded endpoints must not create request spans"
    finally:
        background.shutdown(wait=True)
        external.shutdown()
        external.server_close()
        session.close()
        app_logger.removeHandler(handler)
        app_logger.setLevel(old_level)
        engine.dispose()
        observability.shutdown_observability()
        os.environ.pop("LADIES_OTEL_ENABLED", None)


def check_pyinstrument() -> None:
    names = (
        "LADIES_PYINSTRUMENT",
        "LADIES_PYINSTRUMENT_DIR",
        "LADIES_PYINSTRUMENT_MIN_MS",
        "LADIES_PYINSTRUMENT_INTERVAL",
        "LADIES_PYINSTRUMENT_MAX_FILES",
    )
    try:
        with TemporaryDirectory(prefix="ladies-pyinstrument-") as tmp:
            profile_dir = Path(tmp) / "profiles"
            os.environ["LADIES_PYINSTRUMENT"] = "1"
            os.environ["LADIES_PYINSTRUMENT_DIR"] = str(profile_dir)
            os.environ["LADIES_PYINSTRUMENT_MIN_MS"] = "0"
            os.environ["LADIES_PYINSTRUMENT_INTERVAL"] = "0.001"
            os.environ["LADIES_PYINSTRUMENT_MAX_FILES"] = "2"

            app = Flask("pyinstrument-runtime")

            @app.get("/api/profile/<int:item_id>")
            def profiled_request(item_id: int):
                time.sleep(0.01)
                return jsonify(ok=True, item_id=item_id)

            @app.get("/api/messaging/events")
            def excluded_events():
                return "events"

            @app.post("/api/client-log")
            def excluded_client_log():
                return jsonify(ok=True)

            install_request_profiling(app, default_dir=Path(tmp) / "unused-default")
            client = app.test_client()
            for item_id in (424242, 424243, 424244):
                response = client.get(f"/api/profile/{item_id}?token=profile-secret")
                assert response.status_code == 200
                assert response.get_json() == {"ok": True, "item_id": item_id}

            reports = sorted(profile_dir.glob("*.html"))
            assert len(reports) == 2, "Pyinstrument retention must bound report count"
            for report in reports:
                assert "profiled_request" in report.name
                assert "profile-secret" not in report.name
                report_text = report.read_text(encoding="utf-8")
                assert "pyinstrument" in report_text.lower()
                assert "profile-secret" not in report_text

            before_excluded = [path.name for path in reports]
            assert client.get("/api/messaging/events").status_code == 200
            assert client.post("/api/client-log").status_code == 200
            assert [path.name for path in sorted(profile_dir.glob("*.html"))] == before_excluded

            blocked_path = Path(tmp) / "blocked-profile-dir"
            blocked_path.write_text("not a directory", encoding="utf-8")
            os.environ["LADIES_PYINSTRUMENT_DIR"] = str(blocked_path)
            fail_open = Flask("pyinstrument-fail-open")

            @fail_open.get("/api/fail-open")
            def profiled_fail_open():
                time.sleep(0.005)
                return jsonify(ok=True)

            install_request_profiling(fail_open)
            fail_open_response = fail_open.test_client().get("/api/fail-open")
            assert fail_open_response.status_code == 200
            assert fail_open_response.get_json() == {"ok": True}
    finally:
        for name in names:
            os.environ.pop(name, None)


def main() -> None:
    check_disabled_is_noop()
    check_targeted_boundaries()
    check_tracing()
    check_pyinstrument()
    print("Observability checks passed")


if __name__ == "__main__":
    main()
