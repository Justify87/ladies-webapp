from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .context import PriceContext
from .normalize import canonical_service, extract_durations, extract_money, extract_price_duration_pairs, fold, normalize_text
from .schema import empty_proposal, validate_proposal

INCLUDED_RE = re.compile(r"\b(inkl\.?|inklusive|dabei|inbegriffen)\b", re.I)
EXCLUDED_RE = re.compile(r"\b(nicht|kein(?:e|en|er|es)?|ohne)\b", re.I)
ON_REQUEST_RE = re.compile(r"\b(auf\s+anfrage|nach\s+absprache|auf\s+wunsch)\b", re.I)
NEGOTIABLE_RE = re.compile(r"\b(vb|verhandelbar|verhandlungssache)\b", re.I)
SURCHARGE_WORD_RE = re.compile(r"\b(extra|aufpreis|zuschlag)\b", re.I)
FULL_RE = re.compile(r"\b(preisliste|meine\s+preise|mein\s+preis|aktuelle\s+preise|preise\s+sind)\b", re.I)
PARTIAL_RE = re.compile(r"\b(jetzt|ab\s+jetzt|neu(?:e|er|en)?\s+preis|kostet\s+jetzt|nun)\b", re.I)
TRAVEL_RE = re.compile(r"\b(hausbesuch|hausbesuche|anfahrt|fahrtkosten)\b", re.I)
HOTEL_RE = re.compile(r"\b(hotel|hotelkosten)\b", re.I)
NIGHT_RE = re.compile(r"\b(nacht|nachts|overnight|übernachtung)\b", re.I)


def extract_with_rules(context: PriceContext, *, has_existing_price_list: bool = False) -> dict[str, Any]:
    proposal = empty_proposal()
    offers = []
    services = []
    conditions = []
    uncertainties = []
    seen_offer = set()
    seen_service = set()

    previous_outgoing = None
    previous_outgoing_message = None
    for msg in context.messages:
        raw_text = str(msg.effective_text or "")
        text = normalize_text(raw_text)
        if not text:
            continue
        if msg.direction == "OUT":
            previous_outgoing = text
            previous_outgoing_message = msg
            continue
        source_type = "CHAT_IMAGE_OCR" if msg.message_type == "IMAGE" and msg.ocr_text else "CHAT_TEXT"
        evidence = [{"message_id": msg.message_id, "excerpt": text[:500], "source_type": source_type}]
        durations = extract_durations(text)
        monies = extract_money(text)
        structured_pairs = extract_price_duration_pairs(raw_text)

        for pair in structured_pairs:
            key = (pair["duration_minutes"], pair["amount_cents"], None, "EXACT")
            if key in seen_offer:
                continue
            seen_offer.add(key)
            offers.append({
                "proposal_key": f"offer_{len(offers)+1}",
                "duration_minutes": pair["duration_minutes"],
                "duration_max_minutes": None,
                "amount_type": "EXACT",
                "amount_cents": pair["amount_cents"],
                "amount_max_cents": None,
                "label": f"{pair['duration_minutes']} Minuten",
                "evidence": evidence,
            })

        if not monies and previous_outgoing and re.search(r"\b(kost|preis|wie\s+viel|nimm)\w*\b", fold(previous_outgoing)):
            m = re.fullmatch(r"\s*(\d{2,4})(?:[\.,](\d{1,2}))?\s*", text)
            if m:
                amount = float(m.group(1) + ("." + m.group(2) if m.group(2) else ""))
                monies = [{"amount_cents": int(round(amount * 100)), "amount_type": "EXACT", "amount_max_cents": None,
                           "start": 0, "end": len(text), "raw": text, "explicit": False, "is_surcharge": False}]

        available = [m for m in monies if not m.get("is_surcharge")]
        pair_durations = list(durations)
        if not pair_durations and len(available) == 1 and previous_outgoing:
            question_durations = extract_durations(previous_outgoing)
            if len(question_durations) == 1:
                pair_durations = question_durations
                evidence = [
                    {"message_id": previous_outgoing_message.message_id, "excerpt": previous_outgoing[:500], "source_type": "CHAT_TEXT"},
                    {"message_id": msg.message_id, "excerpt": text[:500], "source_type": source_type},
                ] if previous_outgoing_message is not None else evidence
        # Multiple structured rows are authoritative for this message.  Skipping
        # nearest-neighbour pairing avoids bogus rows from compact text such as
        # "10045 Minuten", where the generic money regex can otherwise see 1004.
        if len(structured_pairs) < 2:
            for d in pair_durations:
                if not available:
                    continue
                money = min(available, key=lambda m: abs(((m["start"] + m["end"]) / 2) - ((d["start"] + d["end"]) / 2)))
                key = (d["minutes"], money["amount_cents"], money.get("amount_max_cents"), money["amount_type"])
                if key in seen_offer:
                    continue
                seen_offer.add(key)
                offers.append({
                    "proposal_key": f"offer_{len(offers)+1}",
                    "duration_minutes": d["minutes"],
                    "duration_max_minutes": None,
                    "amount_type": money["amount_type"],
                    "amount_cents": money["amount_cents"],
                    "amount_max_cents": money.get("amount_max_cents"),
                    "label": f"{d['minutes']} Minuten",
                    "evidence": evidence,
                })

        service_key, alias = canonical_service(text)
        surcharge = next((m for m in monies if m.get("is_surcharge")), None)
        if service_key or alias:
            raw_label = _service_label(text, alias or service_key or "Service")
            if surcharge or SURCHARGE_WORD_RE.search(text):
                status = "SURCHARGE"
                amount = surcharge["amount_cents"] if surcharge else None
            elif INCLUDED_RE.search(text):
                status, amount = "INCLUDED", None
            elif ON_REQUEST_RE.search(text):
                status, amount = "ON_REQUEST", None
            elif NEGOTIABLE_RE.search(text):
                status, amount = "NEGOTIABLE", None
            elif EXCLUDED_RE.search(text):
                status, amount = "EXCLUDED", None
            else:
                status, amount = "UNKNOWN", None
            key = (service_key, raw_label.lower(), status, amount)
            if key not in seen_service:
                seen_service.add(key)
                services.append({
                    "proposal_key": f"service_{len(services)+1}",
                    "service_key": service_key,
                    "raw_label": raw_label,
                    "status": status,
                    "amount_cents": amount,
                    "note": None,
                    "evidence": evidence,
                })

        condition_type = None
        if TRAVEL_RE.search(text): condition_type = "TRAVEL"
        elif HOTEL_RE.search(text): condition_type = "HOTEL"
        elif NIGHT_RE.search(text): condition_type = "NIGHT"
        if condition_type:
            amount = surcharge["amount_cents"] if surcharge else None
            conditions.append({
                "proposal_key": f"condition_{len(conditions)+1}",
                "condition_type": condition_type,
                "label": text[:512],
                "amount_cents": amount,
                "note": None,
                "evidence": evidence,
            })

        if monies and not durations and not structured_pairs and not service_key and not condition_type and len(text) > 8:
            uncertainties.append(f"Unklare Preisangabe: {text[:180]}")

    proposal["offers"] = offers
    proposal["service_rules"] = services
    proposal["conditions"] = conditions
    proposal["uncertainties"] = list(dict.fromkeys(uncertainties))

    combined = "\n".join(m.effective_text for m in context.messages)
    if FULL_RE.search(combined) or (len(offers) >= 2 and not has_existing_price_list):
        proposal["update_mode"] = "FULL"
    elif PARTIAL_RE.search(combined) or (has_existing_price_list and len(offers) <= 1):
        proposal["update_mode"] = "PARTIAL"
    else:
        proposal["update_mode"] = "UNKNOWN"
    return validate_proposal(proposal)


def merge_partial(base: dict[str, Any] | None, patch: dict[str, Any]) -> dict[str, Any]:
    patch = validate_proposal(patch)
    if not base or patch["update_mode"] == "FULL":
        out = deepcopy(patch)
        out["update_mode"] = "FULL"
        return out
    base = validate_proposal(base)
    out = deepcopy(base)
    by_duration = {(x.get("duration_minutes"), x.get("duration_max_minutes")): x for x in out["offers"]}
    for item in patch["offers"]:
        by_duration[(item.get("duration_minutes"), item.get("duration_max_minutes"))] = deepcopy(item)
    out["offers"] = sorted(by_duration.values(), key=lambda x: (x.get("duration_minutes") is None, x.get("duration_minutes") or 99999))

    by_service = {(x.get("service_key") or "", fold(x.get("raw_label"))): x for x in out["service_rules"]}
    for item in patch["service_rules"]:
        by_service[(item.get("service_key") or "", fold(item.get("raw_label")))] = deepcopy(item)
    out["service_rules"] = list(by_service.values())

    by_condition = {(x.get("condition_type"), fold(x.get("label"))): x for x in out["conditions"]}
    for item in patch["conditions"]:
        by_condition[(item.get("condition_type"), fold(item.get("label")))] = deepcopy(item)
    out["conditions"] = list(by_condition.values())
    out["uncertainties"] = list(dict.fromkeys((out.get("uncertainties") or []) + (patch.get("uncertainties") or [])))
    out["update_mode"] = "FULL"
    return validate_proposal(out)


def proposal_diff(before: dict[str, Any] | None, after: dict[str, Any]) -> dict[str, Any]:
    before = validate_proposal(before) if before else empty_proposal()
    after = validate_proposal(after)
    old = {(x.get("duration_minutes"), x.get("duration_max_minutes")): x for x in before["offers"]}
    new = {(x.get("duration_minutes"), x.get("duration_max_minutes")): x for x in after["offers"]}
    offers = []
    for key in sorted(set(old) | set(new), key=lambda k: (k[0] is None, k[0] or 99999)):
        a, b = old.get(key), new.get(key)
        if a == b:
            state = "UNCHANGED"
        elif a is None:
            state = "ADDED"
        elif b is None:
            state = "REMOVED"
        else:
            state = "CHANGED"
        offers.append({"key": list(key), "before": a, "after": b, "state": state})
    return {"offers": offers, "changed": any(x["state"] != "UNCHANGED" for x in offers) or before["service_rules"] != after["service_rules"] or before["conditions"] != after["conditions"]}


def _service_label(text: str, alias: str) -> str:
    labels = {"kussen": "Küssen", "kusse": "Küssen", "kissing": "Küssen", "zk": "Küssen",
              "franzosisch": "Französisch", "franzoesisch": "Französisch", "franz": "Französisch",
              "av": "AV", "anal": "Anal", "massage": "Massage", "gfe": "GFE", "69": "69"}
    match = re.search(re.escape(alias), text, re.I)
    return match.group(0) if match else labels.get(alias.lower(), alias)
