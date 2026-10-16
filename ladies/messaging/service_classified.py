from __future__ import annotations

from sqlalchemy import select

from ladies_v9.models import Listing

from .classification import (
    LADIES_CONFIRMED,
    LADIES_SOURCE,
    NON_LADIES,
    endpoint_status_in_session,
    person_is_ladies,
    reconcile_thread_in_session,
    set_thread_classification_in_session,
)
from .models import ChatMessage, ChatThread, ContactEndpoint, EndpointClassification
from .service_v2 import MessagingService as _CanonicalMessagingService

ASSIGNMENT_AUTO = "AUTO_IDENTITY"
ASSIGNMENT_MANUAL = "MANUAL_LADIES"


class MessagingService(_CanonicalMessagingService):
    """Canonical service with durable Ladies identity boundaries.

    Transport/persistence stays in service_v2. This layer owns the transition
    rules between provider identity and product classification. Automatic identity
    discovery may never override NON_LADIES; an explicit manual Ladies assignment
    is the only supported override and commits identity + classification together.
    """

    @staticmethod
    def _auto_identity_allowed(db, endpoint: ContactEndpoint | None) -> bool:
        if endpoint is None:
            return True
        row = db.get(EndpointClassification, int(endpoint.endpoint_id))
        return row is None or row.status != NON_LADIES

    def _get_thread(
        self,
        db,
        person_id: int | None = None,
        endpoint: ContactEndpoint | None = None,
    ) -> ChatThread:
        thread = super()._get_thread(db, person_id=person_id, endpoint=endpoint)
        reconcile_thread_in_session(
            db,
            thread,
            additional_endpoint_ids=[endpoint.endpoint_id] if endpoint is not None else (),
        )
        return thread

    def _resolve_discovered_phone(
        self,
        db,
        endpoint: ContactEndpoint,
        phone: str | None,
        display_phone: str | None = None,
    ) -> ContactEndpoint:
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
        resolved = super()._resolve_discovered_phone(db, endpoint, phone, display_phone)
        endpoint_status_in_session(db, int(resolved.endpoint_id))
        return resolved

    def _assign_endpoint_in_session(
        self,
        db,
        endpoint: ContactEndpoint,
        person_id: int,
        *,
        add_system_message: bool = True,
        assignment_intent: str = ASSIGNMENT_AUTO,
    ) -> None:
        intent = str(assignment_intent or ASSIGNMENT_AUTO).upper()
        if intent != ASSIGNMENT_MANUAL and not self._auto_identity_allowed(db, endpoint):
            return
        return _CanonicalMessagingService._assign_endpoint_in_session(
            self,
            db,
            endpoint,
            person_id,
            add_system_message=add_system_message,
        )

    def assign_endpoint(self, endpoint_id: int, person_id: int) -> dict:
        """Explicitly assign one reviewed WhatsApp conversation to a Ladies person.

        This is the manual override boundary. Identity movement and the sticky
        LADIES_CONFIRMED classification happen in one DB transaction, so callers
        can never observe an assigned-but-unclassified intermediate state.
        """

        with self.database.session() as db:
            endpoint = db.get(ContactEndpoint, int(endpoint_id))
            if endpoint is None:
                raise KeyError(endpoint_id)
            if not person_is_ladies(db, int(person_id)):
                raise ValueError("WhatsApp-Chats können nur einer Person mit Ladies.de-Provenienz zugeordnet werden")

            temporary = db.scalar(
                select(ChatThread)
                .join(ChatMessage, ChatMessage.thread_id == ChatThread.thread_id)
                .where(
                    ChatThread.person_id.is_(None),
                    ChatMessage.endpoint_id == int(endpoint.endpoint_id),
                )
                .limit(1)
            )
            target = db.scalar(
                select(ChatThread).where(ChatThread.person_id == int(person_id)).limit(1)
            )
            if target is not None:
                # Detached NON_LADIES history remains private and does not block a new Ladies conversation.
                reconcile_thread_in_session(db, target)

            # Manual assignment may override the source conversation's exclusion before the thread merge.
            if temporary is not None:
                set_thread_classification_in_session(
                    db,
                    temporary,
                    LADIES_CONFIRMED,
                    method="manual_assignment",
                    force_endpoints=True,
                    additional_endpoint_ids=[endpoint.endpoint_id],
                )

            self._assign_endpoint_in_session(
                db,
                endpoint,
                int(person_id),
                add_system_message=True,
                assignment_intent=ASSIGNMENT_MANUAL,
            )
            db.flush()

            target = db.scalar(
                select(ChatThread).where(ChatThread.person_id == int(person_id)).limit(1)
            )
            if target is None:
                raise RuntimeError("Ziel-Chat wurde bei der Zuordnung nicht angelegt")
            set_thread_classification_in_session(
                db,
                target,
                LADIES_CONFIRMED,
                method="manual_assignment",
                force_endpoints=True,
                additional_endpoint_ids=[endpoint.endpoint_id],
            )
            return self._endpoint_payload(endpoint)

    def assignment_candidates(self, endpoint_id: int) -> list[dict]:
        items = super().assignment_candidates(endpoint_id)
        person_ids = [int(item.get("person_id") or 0) for item in items if item.get("person_id")]
        if not person_ids:
            return []
        with self.database.session() as db:
            ladies_ids = set(
                int(value)
                for value in db.scalars(
                    select(Listing.person_id)
                    .where(
                        Listing.person_id.in_(person_ids),
                        Listing.source == LADIES_SOURCE,
                    )
                    .distinct()
                ).all()
            )
        return [item for item in items if int(item.get("person_id") or 0) in ladies_ids]


__all__ = ["MessagingService", "ASSIGNMENT_AUTO", "ASSIGNMENT_MANUAL"]
