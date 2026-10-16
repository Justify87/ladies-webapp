from __future__ import annotations

import atexit
from functools import wraps
import ipaddress
import logging
from pathlib import Path

from flask import jsonify, redirect, request

from .background import BackgroundTasks
from .config import frontend_config
from .encounter_metadata import install_encounter_metadata
from .lan_access import LanAccessManager
from .map_view import install_map_view
from .messaging import install_messaging
from .messaging.integration import merge_messaging_person, reconcile_identity_events as reconcile_messaging_identity_events
from .messaging.scope import hydrate_ladies_history, reconcile_ladies_endpoints
from .observability import init_observability
from .pricing import install_pricing
from .pricing.integration import merge_pricing_person, reconcile_identity_events as reconcile_pricing_identity_events
from .profiling import install_request_profiling
from .saved_searches import install_saved_searches
from .search_preview import install_search_preview
from .web import create_app as create_core_app

LOGGER = logging.getLogger("ladies_explorer.messaging.integration")
REQUEST_LOGGER = logging.getLogger("ladies_explorer")
LAN_COOKIE = "ladies_access"
BROWSER_CSP = (
    "frame-ancestors 'self'; "
    "connect-src 'self' https://cdn.jsdelivr.net https://storage.googleapis.com https://tiles.openfreemap.org"
)


class _ExpectedStreamDisconnectFilter(logging.Filter):
    """Suppress Flask's error-level teardown record for a normal SSE disconnect."""

    def filter(self, record: logging.LogRecord) -> bool:
        if "REQUEST exception" not in record.getMessage() or "path=/api/messaging/events" not in record.getMessage():
            return True
        exc_info = record.exc_info
        exc = exc_info[1] if isinstance(exc_info, tuple) and len(exc_info) > 1 else None
        return not isinstance(exc, GeneratorExit)


def _install_request_log_filter() -> None:
    if any(isinstance(item, _ExpectedStreamDisconnectFilter) for item in REQUEST_LOGGER.filters):
        return
    REQUEST_LOGGER.addFilter(_ExpectedStreamDisconnectFilter())


def _is_loopback(remote_addr: str | None) -> bool:
    if not remote_addr:
        return False
    try:
        addr = ipaddress.ip_address(remote_addr)
        if addr.is_loopback:
            return True
        mapped = getattr(addr, "ipv4_mapped", None)
        return bool(mapped and mapped.is_loopback)
    except ValueError:
        return False


def _install_domain_route_integrations(app, *, database, messaging, background) -> None:
    """Attach identity side effects to the routes that establish Ladies identity."""

    def reconcile_identity_state() -> None:
        reconcile_pricing_identity_events(database)
        reconcile_messaging_identity_events(database)

    def hydrate_person(person_id: int) -> None:
        result = hydrate_ladies_history(messaging, int(person_id), limit=10)
        LOGGER.info(
            "WhatsApp history hydrated after Ladies identity ingest person_id=%s restored=%s requested=%s existing=%s",
            person_id,
            result.get("restored", 0),
            result.get("requested", 0),
            result.get("existing", 0),
        )

    profile_view = app.view_functions["api_profile"]

    @wraps(profile_view)
    def profile_with_identity_sync(*args, **kwargs):
        response = app.make_response(profile_view(*args, **kwargs))
        if response.status_code >= 400:
            return response
        try:
            payload = response.get_json(silent=True) or {}
            person_id = (payload.get("identity") or {}).get("person_id")
            if person_id:
                person_id = int(person_id)
                messaging.sync_person(person_id)
                reconcile_identity_state()
                background.submit(
                    f"wa-hydrate:{person_id}",
                    hydrate_person,
                    person_id,
                )
        except Exception:
            LOGGER.exception("Identity reconciliation failed endpoint=api_profile")
        return response

    app.view_functions["api_profile"] = profile_with_identity_sync

    merge_view = app.view_functions["api_identity_merge"]

    @wraps(merge_view)
    def merge_with_identity_sync(*args, **kwargs):
        response = app.make_response(merge_view(*args, **kwargs))
        if response.status_code >= 400:
            return response
        try:
            reconcile_identity_state()
        except Exception:
            LOGGER.exception("Identity reconciliation failed endpoint=api_identity_merge")
        return response

    app.view_functions["api_identity_merge"] = merge_with_identity_sync


def create_app(
    *,
    db_path: str | Path,
    data_dir: str | Path,
    default_city: str = "leipzig",
    default_radius: int = 20,
    profile_delay: float = 2.0,
):
    _install_request_log_filter()
    app = create_core_app(
        db_path=db_path,
        data_dir=data_dir,
        default_city=default_city,
        default_radius=default_radius,
        profile_delay=profile_delay,
    )
    install_encounter_metadata(app, database=app.extensions["ladies_database"])
    install_search_preview(app, default_city=default_city, default_radius=default_radius)

    public_config = frontend_config(default_city=default_city, default_radius=default_radius)
    app.config["APP_VERSION"] = public_config["version"]
    app.config["FRONTEND_CONFIG"] = public_config

    database = app.extensions["ladies_database"]
    init_observability(app, database.engine, service_version=public_config["version"])
    install_request_profiling(
        app,
        default_dir=Path(data_dir).resolve().parent / "logs" / "profiles",
    )

    @app.get("/api/config")
    def api_config():
        return jsonify(public_config)

    access = LanAccessManager(data_dir)
    background = BackgroundTasks(max_workers=2)
    map_background = BackgroundTasks(max_workers=1, max_pending=4)
    app.config["LAN_ACCESS_MANAGER"] = access
    app.config["LAN_ACCESS_TOKEN"] = access.pairing_token
    app.extensions["background_tasks"] = background
    app.extensions["map_background_tasks"] = map_background
    atexit.register(background.shutdown)
    atexit.register(map_background.shutdown)

    install_map_view(app, background=map_background, data_dir=data_dir)

    @app.before_request
    def protect_remote_local_data():
        if _is_loopback(request.remote_addr) or request.path.startswith("/pair/"):
            return None
        if not (request.path.startswith("/api/") or request.path.startswith("/archive/")):
            return None
        if access.validate_session(request.cookies.get(LAN_COOKIE)):
            return None
        return jsonify(error="Dieses Gerät ist noch nicht mit Ladies Explorer gekoppelt."), 401

    @app.after_request
    def browser_runtime_policy(response):
        if not response.headers.get("Content-Security-Policy"):
            response.headers["Content-Security-Policy"] = BROWSER_CSP

        if request.method == "GET" and response.status_code == 200:
            if request.path in {"/api/messaging/threads", "/api/messaging/unassigned"}:
                response.headers["Cache-Control"] = "private, no-store"
            elif request.path in {"/api/dashboard", "/api/storage"}:
                response.headers["Cache-Control"] = "private, max-age=1"
        return response

    @app.get("/pair/<token>")
    def pair_lan_device(token: str):
        session = access.pair(token)
        if session is None:
            return jsonify(error="Ungültiger oder abgelaufener Pairing-Code"), 404
        response = redirect("/", code=302)
        response.set_cookie(
            LAN_COOKIE,
            session,
            max_age=365 * 24 * 60 * 60,
            httponly=True,
            samesite="Strict",
            secure=False,
            path="/",
        )
        return response

    repositories = app.extensions["ladies_repositories"]
    saved_search_scheduler = install_saved_searches(
        app,
        background=background,
        default_city=default_city,
        default_radius=default_radius,
    )
    atexit.register(saved_search_scheduler.shutdown)

    messaging = install_messaging(app, database=database, data_dir=data_dir)
    install_pricing(app, database=database, data_dir=data_dir)

    repositories.persons.set_identity_merge_handlers(
        merge_pricing_person,
        merge_messaging_person,
    )

    try:
        reconcile_pricing_identity_events(database)
        reconcile_messaging_identity_events(database)
        # Repair pre-existing unresolved rows once at startup. Normal request
        # reads never scan all unassigned WhatsApp endpoints.
        reconcile_ladies_endpoints(messaging)
    except Exception:
        LOGGER.exception("Initial identity reconciliation failed")

    _install_domain_route_integrations(
        app,
        database=database,
        messaging=messaging,
        background=background,
    )
    saved_search_scheduler.start()

    return app
