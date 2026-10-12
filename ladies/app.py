from __future__ import annotations

import atexit
import ipaddress
import logging
from pathlib import Path

from flask import jsonify, redirect, request

from .background import BackgroundTasks
from .config import frontend_config
from .lan_access import LanAccessManager
from .messaging import install_messaging
from .messaging.integration import install_identity_merge_listener, reconcile_identity_events
from .pricing import install_pricing, install_pricing_identity_merge_listener
from .search_preview import install_search_preview
from .web import create_app as create_core_app

LOGGER = logging.getLogger("ladies_explorer.messaging.integration")
LAN_COOKIE = "ladies_access"


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


def create_app(
    *,
    db_path: str | Path,
    data_dir: str | Path,
    default_city: str = "leipzig",
    default_radius: int = 20,
    profile_delay: float = 2.0,
):
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
    install_search_preview(app, default_city=default_city, default_radius=default_radius)

    @app.get("/api/config")
    def api_config():
        return jsonify(frontend_config(default_city=default_city, default_radius=default_radius))

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

    def rehydrate_liked_person(person_id: int) -> None:
        result = messaging.rehydrate_recent_history(int(person_id), limit=10)
        LOGGER.info(
            "WhatsApp rehydrate after like person_id=%s restored=%s requested=%s existing=%s",
            person_id,
            result.get("restored", 0),
            result.get("requested", 0),
            result.get("existing", 0),
        )

    @app.after_request
    def reconcile_messaging_after_identity_change(response):
        try:
            if response.status_code < 400 and request.path.startswith("/api/profile/"):
                payload = response.get_json(silent=True) or {}
                person_id = (payload.get("identity") or {}).get("person_id")
                if person_id:
                    messaging.sync_person(int(person_id))
                    reconcile_identity_events(database)
                    pricing.reconcile_identity_events()
            elif response.status_code < 400 and request.path == "/api/identity/merge":
                reconcile_identity_events(database)
                pricing.reconcile_identity_events()
            elif (
                response.status_code < 400
                and request.method == "POST"
                and request.path.startswith("/api/person/")
                and request.path.endswith("/state")
            ):
                submitted = request.get_json(silent=True) or {}
                if str(submitted.get("preference") or "").upper() == "LIKE":
                    parts = request.path.strip("/").split("/")
                    if len(parts) == 4 and parts[:2] == ["api", "person"] and parts[3] == "state":
                        person_id = int(parts[2])
                        background.submit(
                            f"wa-rehydrate:{person_id}",
                            rehydrate_liked_person,
                            person_id,
                        )
        except Exception:
            LOGGER.exception("Identity reconciliation failed path=%s", request.path)
        return response

    return app
