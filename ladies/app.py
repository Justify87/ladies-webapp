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
from .messaging import install_messaging
from .messaging.integration import install_identity_merge_listener, reconcile_identity_events
from .pricing import install_pricing, install_pricing_identity_merge_listener
from .search_preview import install_search_preview
from .web import create_app as create_core_app

LOGGER = logging.getLogger("ladies_explorer.messaging.integration")
REQUEST_LOGGER = logging.getLogger("ladies_explorer")
LAN_COOKIE = "ladies_access"
BROWSER_CSP = "frame-ancestors 'self'; connect-src 'self' https://cdn.jsdelivr.net https://storage.googleapis.com"


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


def _install_domain_route_integrations(app, *, database, messaging, pricing, background) -> None:
    """Attach domain side effects to the routes that own the corresponding mutation.

    This keeps identity/messaging behavior explicit instead of inferring business
    operations later from request paths in a global ``after_request`` hook.
    """

    def reconcile_identity_state() -> None:
        reconcile_identity_events(database)
        pricing.reconcile_identity_events()

    def rehydrate_liked_person(person_id: int) -> None:
        result = messaging.rehydrate_recent_history(int(person_id), limit=10)
        LOGGER.info(
            "WhatsApp rehydrate after like person_id=%s restored=%s requested=%s existing=%s",
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
                messaging.sync_person(int(person_id))
                reconcile_identity_state()
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

    person_state_view = app.view_functions["api_person_state"]

    @wraps(person_state_view)
    def person_state_with_rehydrate(*args, **kwargs):
        submitted = request.get_json(silent=True) or {}
        response = app.make_response(person_state_view(*args, **kwargs))
        if response.status_code >= 400 or str(submitted.get("preference") or "").upper() != "LIKE":
            return response
        try:
            person_id = kwargs.get("person_id")
            if person_id is None and args:
                person_id = args[0]
            if person_id is not None:
                person_id = int(person_id)
                background.submit(
                    f"wa-rehydrate:{person_id}",
                    rehydrate_liked_person,
                    person_id,
                )
        except Exception:
            LOGGER.exception("WhatsApp rehydrate scheduling failed endpoint=api_person_state")
        return response

    app.view_functions["api_person_state"] = person_state_with_rehydrate


def create_app(
    *,
    db_path: str | Path,
    data_dir: str | Path,
    default_city: str = "leipzig",
    default_radius: int = 20,
    profile_delay: float = 2.0,
):
    _install_request_log_filter()
    # Pricing moves extraction refs before messaging may delete a duplicate source thread.
    install_pricing_identity_merge_listener()
    install_identity_merge_listener()
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

    @app.get("/api/config")
    def api_config():
        return jsonify(public_config)

    access = LanAccessManager(data_dir)
    background = BackgroundTasks(max_workers=2)
    app.config["LAN_ACCESS_MANAGER"] = access
    app.config["LAN_ACCESS_TOKEN"] = access.pairing_token
    app.extensions["background_tasks"] = background
    atexit.register(background.shutdown)

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
        # MediaPipe needs jsDelivr + Google model storage, but not Google's ODML
        # telemetry endpoint. Only install the browser-wide policy when the route
        # did not already provide a stricter policy (for example `sandbox` on
        # active document downloads).
        if not response.headers.get("Content-Security-Policy"):
            response.headers["Content-Security-Policy"] = BROWSER_CSP

        # These read-only summaries are triggered by several independent UI
        # mutations. A one-second private browser cache coalesces bursts without
        # turning application state into a long-lived cache.
        if request.method == "GET" and response.status_code == 200 and request.path in {
            "/api/dashboard",
            "/api/storage",
            "/api/messaging/threads",
        }:
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

    database = app.extensions["ladies_database"]
    messaging = install_messaging(app, database=database, data_dir=data_dir)
    pricing = install_pricing(app, database=database, data_dir=data_dir)
    try:
        reconcile_identity_events(database)
        pricing.reconcile_identity_events()
    except Exception:
        LOGGER.exception("Initial identity reconciliation failed")

    _install_domain_route_integrations(
        app,
        database=database,
        messaging=messaging,
        pricing=pricing,
        background=background,
    )

    return app
