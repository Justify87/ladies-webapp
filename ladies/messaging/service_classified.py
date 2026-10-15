from __future__ import annotations

from sqlalchemy import select

from .classification import NON_LADIES
from .models import ContactEndpoint, EndpointClassification
from .service_v2 import MessagingService as _CanonicalMessagingService


class MessagingService(_CanonicalMessagingService):
    """Canonical service with sticky manual identity exclusions.

    This is deliberately narrow: transport/persistence stays in service_v2. The
    only extra rule is that automatic identity discovery cannot override a user's
    explicit NON_LADIES endpoint decision. Explicit/manual assignment still may.
    """

    @staticmethod
    def _auto_identity_allowed(db, endpoint: ContactEndpoint | None) -> bool:
        if endpoint is None:
            return True
        row = db.get(EndpointClassification, int(endpoint.endpoint_id))
        return row is None or row.status != NON_LADIES

    def _resolve_discovered_phone(self, db, endpoint: ContactEndpoint, phone: str | None, display_phone: str | None = None) -> ContactEndpoint:
        if not phone:
            return endpoint
        if not self._auto_identity_allowed(db, endpoint):
            endpoint.display_phone = display_phone or endpoint.display_phone or phone
            return endpoint
        other = db.scalar(
            select(ContactEndpoint).where(
                ContactEndpoint.provider == "WHATSAPP",
                ContactEndpoint.normalized_phone == phone,
                ContactEndpoint.endpoint_id != endpoint.endpoint_id,
            ).limit(1)
        )
        if other is not None and not self._auto_identity_allowed(db, other):
            endpoint.display_phone = display_phone or endpoint.display_phone or phone
            return endpoint
        return super()._resolve_discovered_phone(db, endpoint, phone, display_phone)

    def _assign_endpoint_in_session(self, db, endpoint: ContactEndpoint, person_id: int, *, add_system_message: bool = True) -> None:
        if not add_system_message and not self._auto_identity_allowed(db, endpoint):
            return
        return super()._assign_endpoint_in_session(
            db,
            endpoint,
            person_id,
            add_system_message=add_system_message,
        )


__all__ = ["MessagingService"]
