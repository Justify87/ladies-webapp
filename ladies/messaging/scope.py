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
            .join(Listing, Listing.person_id == PersonIdentifier.person_id)
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
        .where(ChatThread.person_id.is_not(None), ChatMessageMeta.provider_key_json.is_not(None))
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


def reconcile_ladies_endpoints(service) -> int:
    """Heal only unassigned WhatsApp endpoints with unambiguous Ladies evidence."""
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
            if endpoint is not None and _endpoint_candidate_ids(db, endpoint, provider_map):
                visible.append(item)
        return visible


def thread_is_ladies_scoped(database, thread_id: int | None) -> bool:
    """Authorize a thread only when it belongs to, or is unresolved evidence for, Ladies."""
    thread_id = int(thread_id or 0)
    if not thread_id:
        return False
    with database.session() as db:
        thread = db.get(ChatThread, thread_id)
        if thread is None:
            return False
        if thread.person_id is not None:
            return _person_is_ladies(db, int(thread.person_id))
        last = db.scalar(
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread.thread_id)
            .order_by(ChatMessage.timestamp.desc(), ChatMessage.message_id.desc())
            .limit(1)
        )
        endpoint = db.get(ContactEndpoint, int(last.endpoint_id)) if last is not None and last.endpoint_id else None
        if endpoint is None:
            return False
        return bool(_endpoint_candidate_ids(db, endpoint, _provider_person_map(db)))


def _merge_provider_endpoint(service, db, target: ContactEndpoint, other: ContactEndpoint) -> bool:
    if target.person_id is None:
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


def refresh_endpoint_identity(service, endpoint_id: int | None) -> str | None:
    """Resolve PN -> LID and persist the provider address on the existing Ladies endpoint."""
    endpoint_id = int(endpoint_id or 0)
    if not endpoint_id or service.gateway.state != "CONNECTED":
        return None
    with service.database.session() as db:
        endpoint = db.get(ContactEndpoint, endpoint_id)
        if endpoint is None or endpoint.state != "ACTIVE" or not endpoint.normalized_phone:
            return None
        phone_jid = endpoint.normalized_phone.lstrip("+") + "@s.whatsapp.net"
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
    provider_address = str(payload.get("jid") or "").strip()
    if not provider_address:
        return None

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
    return provider_address
