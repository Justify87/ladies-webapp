from __future__ import annotations

from copy import deepcopy
from typing import Any

SCHEMA_VERSION = 1
VALID_UPDATE_MODES = {"FULL", "PARTIAL", "UNKNOWN"}
VALID_AMOUNT_TYPES = {"EXACT", "FROM", "UP_TO", "RANGE", "NEGOTIABLE", "UNKNOWN"}
VALID_SERVICE_STATUSES = {"INCLUDED", "SURCHARGE", "EXCLUDED", "ON_REQUEST", "NEGOTIABLE", "UNKNOWN"}
VALID_CONDITION_TYPES = {"TRAVEL", "HOTEL", "NIGHT", "LOCATION", "MINIMUM", "TIME", "OTHER"}


def empty_proposal() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "currency": "EUR",
        "update_mode": "UNKNOWN",
        "offers": [],
        "service_rules": [],
        "conditions": [],
        "uncertainties": [],
    }


def validate_proposal(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Proposal muss ein Objekt sein")
    proposal = deepcopy(value)
    proposal["schema_version"] = SCHEMA_VERSION
    proposal["currency"] = str(proposal.get("currency") or "EUR").upper()[:8]
    mode = str(proposal.get("update_mode") or "UNKNOWN").upper()
    if mode not in VALID_UPDATE_MODES:
        raise ValueError("Ungültiger update_mode")
    proposal["update_mode"] = mode

    offers = []
    for index, raw in enumerate(proposal.get("offers") or [], start=1):
        if not isinstance(raw, dict):
            continue
        duration = _int_or_none(raw.get("duration_minutes"))
        duration_max = _int_or_none(raw.get("duration_max_minutes"))
        amount = _int_or_none(raw.get("amount_cents"))
        amount_max = _int_or_none(raw.get("amount_max_cents"))
        amount_type = str(raw.get("amount_type") or "EXACT").upper()
        if amount_type not in VALID_AMOUNT_TYPES:
            amount_type = "UNKNOWN"
        if duration is not None and not 0 < duration <= 24 * 60:
            raise ValueError("Unplausible Dauer")
        if duration_max is not None and (duration is None or duration_max < duration or duration_max > 24 * 60):
            raise ValueError("Unplausible Maximaldauer")
        if amount is not None and not 0 <= amount <= 500_000:
            raise ValueError("Unplausibler Preis")
        if amount_max is not None and (amount is None or amount_max < amount or amount_max > 500_000):
            raise ValueError("Unplausibler Maximalpreis")
        if amount is None and amount_type not in {"NEGOTIABLE", "UNKNOWN"}:
            continue
        offers.append({
            "proposal_key": str(raw.get("proposal_key") or f"offer_{index}"),
            "duration_minutes": duration,
            "duration_max_minutes": duration_max,
            "amount_type": amount_type,
            "amount_cents": amount,
            "amount_max_cents": amount_max,
            "label": str(raw.get("label") or "").strip()[:256] or None,
            "evidence": _evidence(raw.get("evidence")),
        })
    proposal["offers"] = offers

    rules = []
    for index, raw in enumerate(proposal.get("service_rules") or [], start=1):
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status") or "UNKNOWN").upper()
        if status not in VALID_SERVICE_STATUSES:
            status = "UNKNOWN"
        amount = _int_or_none(raw.get("amount_cents"))
        if amount is not None and not 0 <= amount <= 500_000:
            raise ValueError("Unplausibler Service-Aufpreis")
        label = str(raw.get("raw_label") or "").strip()[:256]
        if not label:
            continue
        rules.append({
            "proposal_key": str(raw.get("proposal_key") or f"service_{index}"),
            "service_key": str(raw.get("service_key") or "").strip()[:96] or None,
            "raw_label": label,
            "status": status,
            "amount_cents": amount,
            "note": str(raw.get("note") or "").strip()[:2000] or None,
            "evidence": _evidence(raw.get("evidence")),
        })
    proposal["service_rules"] = rules

    conditions = []
    for index, raw in enumerate(proposal.get("conditions") or [], start=1):
        if not isinstance(raw, dict):
            continue
        ctype = str(raw.get("condition_type") or "OTHER").upper()
        if ctype not in VALID_CONDITION_TYPES:
            ctype = "OTHER"
        label = str(raw.get("label") or "").strip()[:512]
        if not label:
            continue
        amount = _int_or_none(raw.get("amount_cents"))
        conditions.append({
            "proposal_key": str(raw.get("proposal_key") or f"condition_{index}"),
            "condition_type": ctype,
            "label": label,
            "amount_cents": amount,
            "note": str(raw.get("note") or "").strip()[:2000] or None,
            "evidence": _evidence(raw.get("evidence")),
        })
    proposal["conditions"] = conditions
    proposal["uncertainties"] = [str(x)[:1000] for x in (proposal.get("uncertainties") or []) if str(x).strip()]
    return proposal


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(round(float(value)))


def _evidence(value: Any) -> list[dict[str, Any]]:
    out = []
    for raw in value or []:
        if not isinstance(raw, dict):
            continue
        try:
            message_id = int(raw["message_id"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({
            "message_id": message_id,
            "excerpt": str(raw.get("excerpt") or "")[:500],
            "source_type": str(raw.get("source_type") or "CHAT_TEXT")[:32],
        })
    return out
