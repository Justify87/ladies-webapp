from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import secrets
from pathlib import Path

from flask import jsonify, redirect, request

from .messaging import install_messaging
from .messaging.integration import install_identity_merge_listener, reconcile_identity_events
from .web import create_app as create_v91_app

LOGGER = logging.getLogger("ladies_explorer.messaging.integration")
LAN_COOKIE = "ladies_access"


def _load_or_create_access_token(data_dir: str | Path) -> str:
    path = Path(data_dir) / "lan_access_token"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = path.read_text(encoding="utf-8").strip()
        if len(value) >= 24:
            return value
    except OSError:
        pass
    value = secrets.token_urlsafe(32)
    path.write_text(value + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


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


def create_app(*, db_path: str | Path, data_dir: str | Path, default_city: str = "leipzig", default_radius: int = 20, profile_delay: float = 2.0):
    install_identity_merge_listener()
    app = create_v91_app(
        db_path=db_path,
        data_dir=data_dir,
        default_city=default_city,
        default_radius=default_radius,
        profile_delay=profile_delay,
    )

    access_token = _load_or_create_access_token(data_dir)
    app.config["LAN_ACCESS_TOKEN"] = access_token

    @app.before_request
    def protect_remote_local_data():
        # Localhost remains zero-friction. Remote/LAN clients pair once and then
        # use an HttpOnly cookie; sensitive APIs and every archived file are not
        # exposed merely because the server is bound to 0.0.0.0.
        if _is_loopback(request.remote_addr) or request.path.startswith("/pair/"):
            return None
        if not (request.path.startswith("/api/") or request.path.startswith("/archive/")):
            return None
        supplied = request.cookies.get(LAN_COOKIE, "")
        if supplied and hmac.compare_digest(supplied, access_token):
            return None
        return jsonify(error="Dieses Gerät ist noch nicht mit Ladies Explorer gekoppelt."), 401

    @app.get("/pair/<token>")
    def pair_lan_device(token: str):
        if not hmac.compare_digest(str(token), access_token):
            return jsonify(error="Ungültiger Pairing-Code"), 404
        response = redirect("/", code=302)
        response.set_cookie(
            LAN_COOKIE,
            access_token,
            max_age=365 * 24 * 60 * 60,
            httponly=True,
            samesite="Strict",
            secure=False,
            path="/",
        )
        return response

    messaging = install_messaging(app, db_path=db_path, data_dir=data_dir)
    try:
        reconcile_identity_events(messaging.database)
    except Exception:
        LOGGER.exception("Initial messaging identity reconciliation failed")

    @app.after_request
    def reconcile_messaging_after_identity_change(response):
        try:
            if response.status_code < 400 and request.path.startswith("/api/profile/"):
                payload = response.get_json(silent=True) or {}
                person_id = (payload.get("identity") or {}).get("person_id")
                if person_id:
                    messaging.sync_person(int(person_id))
                    reconcile_identity_events(messaging.database)
            elif response.status_code < 400 and request.path == "/api/identity/merge":
                reconcile_identity_events(messaging.database)
        except Exception:
            LOGGER.exception("Messaging identity reconciliation failed path=%s", request.path)
        return response

    return app
