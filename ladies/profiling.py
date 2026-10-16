from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import time

from flask import g, request

from .observability import current_trace_id

LOGGER = logging.getLogger("ladies_explorer.profiling")
_TRUE_VALUES = {"1", "true", "yes", "on"}
_EXCLUDED_PATHS = {
    "/api/messaging/events",
    "/api/client-log",
}


def _enabled() -> bool:
    return str(os.environ.get("LADIES_PYINSTRUMENT") or "").strip().lower() in _TRUE_VALUES


def _float_env(name: str, default: float, *, minimum: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _int_env(name: str, default: int, *, minimum: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip("-._")
    return cleaned[:80] or "request"


def _should_profile_request() -> bool:
    path = request.path
    return path.startswith("/api/") and path not in _EXCLUDED_PATHS


def _prune_reports(profile_dir: Path, max_files: int) -> None:
    reports = sorted(profile_dir.glob("*.html"), key=lambda path: path.name)
    for stale in reports[:-max_files]:
        try:
            stale.unlink()
        except OSError:
            LOGGER.debug("Could not remove stale Pyinstrument report file=%s", stale, exc_info=True)


def install_request_profiling(app, *, default_dir: str | Path | None = None) -> None:
    if not _enabled():
        return

    from pyinstrument import Profiler

    configured_dir = os.environ.get("LADIES_PYINSTRUMENT_DIR")
    profile_dir = Path(configured_dir) if configured_dir else Path(default_dir or "logs/profiles")
    minimum_ms = _float_env("LADIES_PYINSTRUMENT_MIN_MS", 100.0, minimum=0.0)
    interval = _float_env("LADIES_PYINSTRUMENT_INTERVAL", 0.001, minimum=0.0001)
    max_files = _int_env("LADIES_PYINSTRUMENT_MAX_FILES", 50, minimum=1)

    @app.before_request
    def _start_request_profile() -> None:
        if not _should_profile_request():
            return
        try:
            profiler = Profiler(interval=interval)
            profiler.start()
        except Exception:
            LOGGER.warning("Pyinstrument could not start endpoint=%s", request.endpoint, exc_info=True)
            return
        g.ladies_profiler = profiler
        g.ladies_profiler_started_at = time.perf_counter()

    @app.after_request
    def _finish_request_profile(response):
        profiler = getattr(g, "ladies_profiler", None)
        if profiler is None:
            return response
        try:
            if profiler.is_running:
                profiler.stop()
            elapsed_ms = (time.perf_counter() - g.ladies_profiler_started_at) * 1000.0
            if elapsed_ms < minimum_ms:
                return response

            profile_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            endpoint = _safe_component(request.endpoint or "request")
            trace_id = current_trace_id() or "no-trace"
            filename = (
                f"{stamp}_{request.method.lower()}_{endpoint}_"
                f"{elapsed_ms:.0f}ms_{trace_id[:12]}.html"
            )
            destination = profile_dir / filename
            destination.write_text(profiler.output_html(), encoding="utf-8")
            _prune_reports(profile_dir, max_files)
            LOGGER.info(
                "Pyinstrument profile written endpoint=%s elapsed_ms=%.1f file=%s",
                request.endpoint,
                elapsed_ms,
                destination,
            )
        except Exception:
            LOGGER.warning("Pyinstrument report failed endpoint=%s", request.endpoint, exc_info=True)
        return response

    @app.teardown_request
    def _stop_aborted_profile(_exc) -> None:
        profiler = getattr(g, "ladies_profiler", None)
        if profiler is None or not profiler.is_running:
            return
        try:
            profiler.stop()
        except Exception:
            LOGGER.debug("Pyinstrument teardown stop failed", exc_info=True)

    LOGGER.info(
        "Pyinstrument request profiling enabled min_ms=%.1f interval=%.4f max_files=%s dir=%s",
        minimum_ms,
        interval,
        max_files,
        profile_dir,
    )
