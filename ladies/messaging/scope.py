from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select, update

from ladies_v9.models import Listing, PersonIdentifier, utcnow

from .models import ChatMessage, ChatMessageMeta, ChatThread, ContactEndpoint

LOGGER = logging.getLogger("ladies_explorer.messaging.scope")
LADIES_SOURCE = "LADIES"
_PROVIDER_KEY_FIELDS = ("remoteJid", "remote_jid", "remoteJidAlt", "remote_jid_alt")


def _person_is_ladies(db, person_id: int | None) -> bool:
    if not person_id:
        return False
    return db.scalar(
        select(Listing.listing_id)
        .where(Listing.person_id == int(person_id), Listing.source == LADIES_SOURCE)
        .limit(1)
    ) is not None


def person_is_ladies_scoped(database, person_id: int | None) -> bool:
    with database.session() as db:
        return _person_is_ladies(db, int(person_id or 0))


def _phone_person_ids(db, phone: str | None) -> set[int]:
    if not phone:
        return set()
    return set(
        db.scalars(
            select(PersonIdentifier.person_id)
            .join(Listing, Listing.listing_id == PersonIdentifier.listing_id)
            .where(
                PersonIdentifier.kind == "PHONE",
                PersonIdentifier.normalized_value == phone,
                Listing.source == LADIES_SOURCE,
            )
            .distinct()
        ).all()
    )


def _provider_person_map(db) -> dict[str, set[int]]:
    rows = db.execute(
        select(ChatMessageMeta.provider_key_json, ChatThread.person_id)
        .join(ChatMessage, ChatMessage.message_id == ChatMessageMeta.message_id)
        .join(ChatThread, ChatThread.thread_id == ChatMessage.thread_id)
        .where(
            ChatThread.person_id.is_not(None),
            ChatMessage.direction == "OUT",
            ChatMessageMeta.provider_key_json.is_not(None),
        )
    ).all()
    ladies_cache: dict[int, bool] = {}
    result: dict[str, set[int]] = {}
    for raw, person_id in rows:
        pid = int(person_id or 0)
        if not pid:
            continue
        if pid not in ladies_cache:
            ladies_cache[pid] = _person_is_ladies(db, pid)
        if not ladies_cache[pid]:
            continue
        try:
            key = json.loads(raw or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(key, dict):
            continue
        for field in _PROVIDER_KEY_FIELDS:
            address = str(key.get(field) or "").strip()
            if address:
                result.setdefault(address, set()).add(pid)
    return result


def _endpoint_candidate_ids(
    db,
    endpoint: ContactEndpoint,
    provider_map: dict[str, set[int]] | None = None,
) -> set[int]:
    candidates: set[int] = set()
    if endpoint.source_listing_id:
        listing = db.get(Listing, int(endpoint.source_listing_id))
        if listing is not None and listing.source == LADIES_SOURCE:
            candidates.add(int(listing.person_id))
    candidates.update(_phone_person_ids(db, endpoint.normalized_phone))
    if endpoint.provider_address:
        candidates.update((provider_map or {}).get(str(endpoint.provider_address), set()))
    return candidates


def _endpoint_is_ladies(db, endpoint: ContactEndpoint | None, provider_map=None) -> bool:
    if endpoint is None:
        return False
    if endpoint.person_id is not None:
        return _person_is_ladies(db, int(endpoint.person_id))
    candidates = _endpoint_candidate_ids(db, endpoint, provider_map)
    return bool(candidates)


def endpoint_is_ladies_scoped(database, endpoint_id: int | None) -> bool:
    endpoint_id = int(endpoint_id or 0)
    if not endpoint_id:
        return False
    with database.session() as db:
        endpoint = db.get(ContactEndpoint, endpoint_id)
        if endpoint is None:
            return False
        provider_map = _provider_person_map(db) if endpoint.provider_address else {}
        return _endpoint_is_ladies(db, endpoint, provider_map)


def _thread_is_ladies(db, thread: ChatThread | None, provider_map=None) -> bool:
    if thread is None:
        return False
    if thread.person_id is not None:
        return _person_is_ladies(db, int(thread.person_id))
    endpoint_ids = db.scalars(
        select(ChatMessage.endpoint_id)
        .where(ChatMessage.thread_id == thread.thread_id, ChatMessage.endpoint_id.is_not(None))
        .distinct()
    ).all()
    for endpoint_id in endpoint_ids:
        endpoint = db.get(ContactEndpoint, int(endpoint_id))
        if _endpoint_is_ladies(db, endpoint, provider_map):
            return True
    return False


def thread_is_ladies_scoped(database, thread_id: int | None) -> bool:
    thread_id = int(thread_id or 0)
    if not thread_id:
        return False
    with database.session() as db:
        thread = db.get(ChatThread, thread_id)
        if thread is None:
            return False
        provider_map = _provider_person_map(db) if thread.person_id is None else {}
        return _thread_is_ladies(db, thread, provider_map)


def message_is_ladies_scoped(database, message_id: int | None) -> bool:
    message_id = int(message_id or 0)
    if not message_id:
        return False
    with database.session() as db:
        message = db.get(ChatMessage, message_id)
        if message is None:
            return False
        thread = db.get(ChatThread, int(message.thread_id))
        provider_map = _provider_person_map(db) if thread is not None and thread.person_id is None else {}
        return _thread_is_ladies(db, thread, provider_map)


def event_is_ladies_scoped(database, event: dict[str, Any]) -> bool:
    """Filter resource-bound browser events; global connection events stay visible."""
    if not isinstance(event, dict):
        return False
    if event.get("thread_id") is not None:
        return thread_is_ladies_scoped(database, event.get("thread_id"))
    if event.get("message_id") is not None:
        return message_is_ladies_scoped(database, event.get("message_id"))
    if event.get("endpoint_id") is not None:
        return endpoint_is_ladies_scoped(database, event.get("endpoint_id"))
    return True


def reconcile_ladies_endpoints(service) -> int:
    """One-shot repair for unresolved endpoints with unambiguous Ladies evidence."""
    with service.database.session() as db:
        provider_map = _provider_person_map(db)
        repairs: list[tuple[int, int]] = []
        for endpoint in db.scalars(
            select(ContactEndpoint).where(
                ContactEndpoint.provider == "WHATSAPP",
                ContactEndpoint.person_id.is_(None),
                ContactEndpoint.state == "ACTIVE",
            )
        ).all():
            candidates = _endpoint_candidate_ids(db, endpoint, provider_map)
            if len(candidates) == 1:
                repairs.append((int(endpoint.endpoint_id), int(next(iter(candidates)))))

    repaired = 0
    for endpoint_id, person_id in repairs:
        try:
            with service.database.session() as db:
                endpoint = db.get(ContactEndpoint, endpoint_id)
                if endpoint is None or endpoint.person_id is not None or endpoint.state != "ACTIVE":
                    continue
                service._assign_endpoint_in_session(
                    db,
                    endpoint,
                    person_id,
                    add_system_message=False,
                )
                repaired += 1
        except Exception:
            LOGGER.warning(
                "Ladies endpoint reconciliation rejected endpoint_id=%s person_id=%s",
                endpoint_id,
                person_id,
                exc_info=True,
            )
    return repaired


def scoped_thread_items(database, items: list[dict[str, Any]], *, include_unassigned: bool) -> list[dict[str, Any]]:
    """Expose only conversations backed by Ladies identity evidence."""
    with database.session() as db:
        provider_map = _provider_person_map(db) if include_unassigned else {}
        ladies_cache: dict[int, bool] = {}
        visible: list[dict[str, Any]] = []
        for item in items:
            person_id = int(item.get("person_id") or 0)
            if person_id:
                if person_id not in ladies_cache:
                    ladies_cache[person_id] = _person_is_ladies(db, person_id)
                if ladies_cache[person_id]:
                    visible.append(item)
                continue
            if not include_unassigned:
                continue
            endpoint_id = int((item.get("endpoint") or {}).get("endpoint_id") or 0)
            endpoint = db.get(ContactEndpoint, endpoint_id) if endpoint_id else None
            if _endpoint_is_ladies(db, endpoint, provider_map):
                visible.append(item)
        return visible


def _merge_provider_endpoint(service, db, target: ContactEndpoint, other: ContactEndpoint) -> bool:
    if target.person_id is None:
        return False
    if (
        target.normalized_phone
        and other.normalized_phone
        and target.normalized_phone != other.normalized_phone
    ):
        LOGGER.warning(
            "Refusing provider merge across different phones target=%s other=%s",
            target.normalized_phone,
            other.normalized_phone,
        )
        return False
    if other.person_id is None:
        service._assign_endpoint_in_session(
            db,
            other,
            int(target.person_id),
            add_system_message=False,
        )
    if other.person_id != target.person_id:
        return False

    provider_address = other.provider_address
    normalized_phone = other.normalized_phone
    display_phone = other.display_phone
    last_seen_at = other.last_seen_at
    db.execute(
        update(ChatMessage)
        .where(ChatMessage.endpoint_id == other.endpoint_id)
        .values(endpoint_id=target.endpoint_id)
    )
    db.delete(other)
    db.flush()

    target.provider_address = provider_address or target.provider_address
    if not target.normalized_phone and normalized_phone:
        target.normalized_phone = normalized_phone
    if not target.display_phone and display_phone:
        target.display_phone = display_phone
    if last_seen_at and (not target.last_seen_at or last_seen_at > target.last_seen_at):
        target.last_seen_at = last_seen_at
    target.updated_at = utcnow()
    return True


def bind_provider_address(service, endpoint_id: int | None, provider_address: str | None) -> str | None:
    endpoint_id = int(endpoint_id or 0)
    provider_address = str(provider_address or "").strip()
    if not endpoint_id or not provider_address:
        return None
    try:
        with service.database.session() as db:
            target = db.get(ContactEndpoint, endpoint_id)
            if target is None or target.state != "ACTIVE":
                return None
            other = db.scalar(
                select(ContactEndpoint).where(
                    ContactEndpoint.provider == "WHATSAPP",
                    ContactEndpoint.provider_address == provider_address,
                    ContactEndpoint.endpoint_id != target.endpoint_id,
                ).limit(1)
            )
            if other is not None:
                if target.person_id is None or (other.person_id is not None and other.person_id != target.person_id):
                    LOGGER.warning(
                        "WhatsApp provider address conflict address=%s target_person=%s other_person=%s",
                        provider_address,
                        target.person_id,
                        other.person_id,
                    )
                    return None
                if not _merge_provider_endpoint(service, db, target, other):
                    return None
            target.provider_address = provider_address
            target.last_seen_at = utcnow()
            target.updated_at = utcnow()
    except Exception:
        LOGGER.warning("WhatsApp identity persistence failed endpoint_id=%s", endpoint_id, exc_info=True)
        return None
    return provider_address


def refresh_endpoint_identity(service, endpoint_id: int | None) -> str | None:
    """Resolve PN -> LID and persist it without ever blocking the actual message flow."""
    endpoint_id = int(endpoint_id or 0)
    if not endpoint_id or service.gateway.state != "CONNECTED":
        return None
    try:
        with service.database.session() as db:
            endpoint = db.get(ContactEndpoint, endpoint_id)
            if endpoint is None or endpoint.state != "ACTIVE" or not endpoint.normalized_phone:
                return None
            if endpoint.provider_address and str(endpoint.provider_address).endswith("@lid"):
                return str(endpoint.provider_address)
            phone_jid = endpoint.normalized_phone.lstrip("+") + "@s.whatsapp.net"
    except Exception:
        LOGGER.warning("WhatsApp identity preflight failed endpoint_id=%s", endpoint_id, exc_info=True)
        return None

    try:
        event = service.gateway.command(
            "contact_info",
            jid=phone_jid,
            include_profile_picture=False,
            timeout=8,
        )
    except Exception:
        LOGGER.debug("WhatsApp identity refresh failed endpoint_id=%s", endpoint_id, exc_info=True)
        return None
    payload = event.get("payload") or {}
    if payload.get("exists") is False:
        return None
    return bind_provider_address(service, endpoint_id, payload.get("jid"))


def hydrate_ladies_history(service, person_id: int, limit: int = 10) -> dict[str, Any]:
    """Hydrate cached WhatsApp history as soon as a Ladies identity becomes known."""
    person_id = int(person_id)
    if not person_is_ladies_scoped(service.database, person_id):
        return {"person_id": person_id, "requested": 0, "restored": 0, "existing": 0}
    service.sync_person(person_id)
    with service.database.session() as db:
        endpoint_ids = list(db.scalars(
            select(ContactEndpoint.endpoint_id).where(
                ContactEndpoint.person_id == person_id,
                ContactEndpoint.provider == "WHATSAPP",
                ContactEndpoint.state == "ACTIVE",
            )
        ).all())
    for endpoint_id in endpoint_ids:
        refresh_endpoint_identity(service, int(endpoint_id))
    return service.rehydrate_recent_history(person_id, limit=limit)
