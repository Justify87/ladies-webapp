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
from .messaging.integration import merge_messaging_person, reconcile_identity_events as reconcile_messaging_identity_events
from .messaging.scope import (
    person_is_ladies_scoped,
    reconcile_ladies_endpoints,
    refresh_endpoint_identity,
    scoped_thread_items,
    thread_is_ladies_scoped,
)
from .pricing import install_pricing
from .pricing.integration import merge_pricing_person, reconcile_identity_events as reconcile_pricing_identity_events
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


def _positive_int(value) -> int | None:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _thread_id_from_call(args, kwargs) -> int | None:
    if "thread_id" in kwargs:
        return _positive_int(kwargs.get("thread_id"))
    return _positive_int(args[0]) if args else None


def _install_domain_route_integrations(app, *, database, messaging, background) -> None:
    """Attach domain side effects and the Ladies-only boundary to messaging routes."""

    def reconcile_identity_state() -> None:
        # Pricing needs the source chat thread before messaging may collapse it.
        reconcile_pricing_identity_events(database)
        reconcile_messaging_identity_events(database)

    def rehydrate_chat_person(person_id: int) -> None:
        result = messaging.rehydrate_recent_history(int(person_id), limit=10)
        LOGGER.info(
            "WhatsApp rehydrate after chat open person_id=%s restored=%s requested=%s existing=%s",
            person_id,
            result.get("restored", 0),
            result.get("requested", 0),
            result.get("existing", 0),
        )

    def scoped_thread_or_404(args, kwargs):
        thread_id = _thread_id_from_call(args, kwargs)
        if thread_is_ladies_scoped(database, thread_id):
            return None
        return jsonify(error="Chat ist keiner Ladies-Person zugeordnet."), 404

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

    threads_view = app.view_functions["messaging_threads"]

    @wraps(threads_view)
    def messaging_threads_scoped(*args, **kwargs):
        filter_name = str(request.args.get("filter") or "ALL").upper()
        try:
            reconcile_ladies_endpoints(messaging)
            response = app.make_response(threads_view(*args, **kwargs))
            if response.status_code >= 400:
                return response
            payload = response.get_json(silent=True) or {}
            payload["items"] = scoped_thread_items(
                database,
                payload.get("items") or [],
                include_unassigned=filter_name == "UNASSIGNED",
            )
            return jsonify(payload)
        except Exception:
            LOGGER.exception("Ladies messaging scope failed filter=%s", filter_name)
            return jsonify(items=[])

    app.view_functions["messaging_threads"] = messaging_threads_scoped

    unassigned_view = app.view_functions["messaging_unassigned"]

    @wraps(unassigned_view)
    def messaging_unassigned_scoped(*args, **kwargs):
        try:
            reconcile_ladies_endpoints(messaging)
            response = app.make_response(unassigned_view(*args, **kwargs))
            if response.status_code >= 400:
                return response
            payload = response.get_json(silent=True) or {}
            payload["items"] = scoped_thread_items(
                database,
                payload.get("items") or [],
                include_unassigned=True,
            )
            return jsonify(payload)
        except Exception:
            LOGGER.exception("Ladies unassigned messaging scope failed")
            return jsonify(items=[])

    app.view_functions["messaging_unassigned"] = messaging_unassigned_scoped

    thread_view = app.view_functions["messaging_thread"]

    @wraps(thread_view)
    def messaging_thread_scoped(*args, **kwargs):
        reconcile_ladies_endpoints(messaging)
        denied = scoped_thread_or_404(args, kwargs)
        if denied is not None:
            return denied
        return thread_view(*args, **kwargs)

    app.view_functions["messaging_thread"] = messaging_thread_scoped

    person_thread_view = app.view_functions["messaging_person_thread"]

    @wraps(person_thread_view)
    def person_thread_with_identity(*args, **kwargs):
        person_id = _positive_int(kwargs.get("person_id") if "person_id" in kwargs else (args[0] if args else None))
        if not person_is_ladies_scoped(database, person_id):
            return jsonify(error="Person gehört zu keiner Ladies-Anzeige."), 404
        response = app.make_response(person_thread_view(*args, **kwargs))
        if response.status_code >= 400:
            return response
        payload = response.get_json(silent=True) or {}
        endpoint_id = _positive_int((payload.get("endpoint") or {}).get("endpoint_id"))
        if endpoint_id:
            refresh_endpoint_identity(messaging, endpoint_id)
        if person_id:
            background.submit(
                f"wa-rehydrate:{person_id}",
                rehydrate_chat_person,
                person_id,
            )
        return response

    app.view_functions["messaging_person_thread"] = person_thread_with_identity

    send_view = app.view_functions["messaging_send"]

    @wraps(send_view)
    def messaging_send_with_identity(*args, **kwargs):
        denied = scoped_thread_or_404(args, kwargs)
        if denied is not None:
            return denied
        submitted = request.get_json(silent=True) or {}
        endpoint_id = _positive_int(submitted.get("endpoint_id"))
        if endpoint_id:
            refresh_endpoint_identity(messaging, endpoint_id)
        response = app.make_response(send_view(*args, **kwargs))
        if response.status_code < 400:
            payload = response.get_json(silent=True) or {}
            sent_endpoint_id = _positive_int((payload.get("message") or {}).get("endpoint_id")) or endpoint_id
            if sent_endpoint_id:
                refresh_endpoint_identity(messaging, sent_endpoint_id)
        return response

    app.view_functions["messaging_send"] = messaging_send_with_identity

    send_media_view = app.view_functions["messaging_send_media"]

    @wraps(send_media_view)
    def messaging_send_media_with_identity(*args, **kwargs):
        denied = scoped_thread_or_404(args, kwargs)
        if denied is not None:
            return denied
        endpoint_id = _positive_int(request.form.get("endpoint_id"))
        if endpoint_id:
            refresh_endpoint_identity(messaging, endpoint_id)
        response = app.make_response(send_media_view(*args, **kwargs))
        if response.status_code < 400:
            payload = response.get_json(silent=True) or {}
            sent_endpoint_id = _positive_int((payload.get("message") or {}).get("endpoint_id")) or endpoint_id
            if sent_endpoint_id:
                refresh_endpoint_identity(messaging, sent_endpoint_id)
        return response

    app.view_functions["messaging_send_media"] = messaging_send_media_with_identity

    for endpoint_name in ("messaging_read", "messaging_presence"):
        original = app.view_functions[endpoint_name]

        @wraps(original)
        def scoped_thread_action(*args, __original=original, **kwargs):
            denied = scoped_thread_or_404(args, kwargs)
            if denied is not None:
                return denied
            return __original(*args, **kwargs)

        app.view_functions[endpoint_name] = scoped_thread_action


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

    database = app.extensions["ladies_database"]
    repositories = app.extensions["ladies_repositories"]
    messaging = install_messaging(app, database=database, data_dir=data_dir)
    install_pricing(app, database=database, data_dir=data_dir)

    repositories.persons.set_identity_merge_handlers(
        merge_pricing_person,
        merge_messaging_person,
    )

    try:
        reconcile_pricing_identity_events(database)
        reconcile_messaging_identity_events(database)
    except Exception:
        LOGGER.exception("Initial identity reconciliation failed")

    _install_domain_route_integrations(
        app,
        database=database,
        messaging=messaging,
        background=background,
    )

    return app
