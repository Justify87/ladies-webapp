from __future__ import annotations

import logging
from pathlib import Path

from flask import request

from .messaging import install_messaging
from .messaging.integration import install_identity_merge_listener, reconcile_identity_events
from .web import create_app as create_v91_app

LOGGER = logging.getLogger("ladies_explorer.messaging.integration")


def create_app(*, db_path: str | Path, data_dir: str | Path, default_city: str = "leipzig", default_radius: int = 20, profile_delay: float = 2.0):
    # Install before the first repository merge can happen. The listener only
    # reacts to V9 IdentityEvent merge actions and deliberately ignores splits.
    install_identity_merge_listener()
    app = create_v91_app(
        db_path=db_path,
        data_dir=data_dir,
        default_city=default_city,
        default_radius=default_radius,
        profile_delay=profile_delay,
    )
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
                # Usually already handled transactionally by before_flush; this
                # remains as an idempotent repair pass for legacy/out-of-band data.
                reconcile_identity_events(messaging.database)
        except Exception:
            LOGGER.exception("Messaging identity reconciliation failed path=%s", request.path)
        return response

    return app
