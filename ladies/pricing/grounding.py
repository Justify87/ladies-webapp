from __future__ import annotations

import unicodedata
from typing import Any

from sqlalchemy import select

from ladies_v9.messaging.models import ChatMessage

from .models import ChatMessageDerived, PriceExtraction, PriceExtractionMessage


def _plain(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    chars: list[str] = []
    previous_space = False
    for ch in text:
        if ch.isalnum() or ch in {",", "."}:
            chars.append(ch)
            previous_space = False
        elif not previous_space:
            chars.append(" ")
            previous_space = True
    return " ".join("".join(chars).split())


def _numeric_values(value: str | None) -> tuple[float, ...]:
    text = unicodedata.normalize("NFKC", str(value or ""))
    current: list[str] = []
    values: list[float] = []

    def flush() -> None:
        if not current:
            return
        raw = "".join(current).strip(".,").replace(",", ".")
        current.clear()
        if not raw or raw.count(".") > 1:
            return
        try:
            values.append(float(raw))
        except ValueError:
            return

    for ch in text:
        if ch.isdigit() or ch in {",", "."}:
            current.append(ch)
        else:
            flush()
    flush()
    return tuple(values)


def _amount_is_grounded(amount_cents: int | None, evidence_text: str) -> bool:
    if amount_cents is None:
        return True
    target = int(amount_cents) / 100
    return any(abs(value - target) < 0.005 for value in _numeric_values(evidence_text))


def _duration_is_grounded(duration_minutes: int | None, evidence_text: str) -> bool:
    if duration_minutes is None:
        return True
    duration = int(duration_minutes)
    values = _numeric_values(evidence_text)
    if any(abs(value - duration) < 0.001 for value in values):
        return True

    plain = _plain(evidence_text)
    compact = plain.replace(" ", "")
    phrases = {
        15: ("viertel stunde", "viertel std"),
        30: ("halbe stunde", "halbe std", "eine halbe stunde", "ne halbe stunde"),
        45: ("dreiviertel stunde", "dreiviertel std"),
        60: ("eine stunde", "eine std", "ne stunde", "ne std"),
        90: ("anderthalb stunden", "anderthalb std", "eineinhalb stunden", "eineinhalb std"),
        120: ("zwei stunden", "zwei std"),
    }
    if any(phrase in plain for phrase in phrases.get(duration, ())):
        return True
    compact_tokens = {
        60: ("1h", "1std", "1stunde"),
        90: ("1,5h", "1.5h", "1,5std", "1.5std"),
        120: ("2h", "2std", "2stunden"),
    }
    return any(token in compact for token in compact_tokens.get(duration, ()))


def _service_label_is_grounded(raw_label: str | None, evidence_text: str) -> bool:
    label = _plain(raw_label)
    if not label:
        return False
    return label in _plain(evidence_text)


def validate_proposal_grounding(db, extraction: PriceExtraction, proposal: dict[str, Any], effective_text) -> None:
    """Validate FunctionGemma evidence without re-extracting pricing semantics.

    FunctionGemma remains the only semantic extractor. This validator merely
    verifies that every cited excerpt is real and that numeric/service values
    emitted by the model are present in the combined cited evidence.
    """

    allowed = set(db.scalars(
        select(PriceExtractionMessage.message_id).where(
            PriceExtractionMessage.price_extraction_id == extraction.price_extraction_id
        )
    ).all())
    message_cache: dict[int, str] = {}

    def evidence_text(item: dict[str, Any]) -> str:
        rows = item.get("evidence") or []
        if not rows:
            raise ValueError("FunctionGemma-Ausgabe enthält eine Angabe ohne Quellbeleg")
        excerpts: list[str] = []
        for evidence in rows:
            message_id = int(evidence.get("message_id") or 0)
            if message_id not in allowed:
                raise ValueError("Proposal verweist auf eine fremde Nachricht")
            if message_id not in message_cache:
                message = db.get(ChatMessage, message_id)
                derived = db.get(ChatMessageDerived, message_id)
                message_cache[message_id] = effective_text(message, derived) if message else ""
            excerpt = str(evidence.get("excerpt") or "").strip()
            source = message_cache[message_id]
            if not excerpt or _plain(excerpt) not in _plain(source):
                raise ValueError("FunctionGemma-Beleg ist nicht wortgetreu in der Quellnachricht enthalten")
            excerpts.append(excerpt)
        return "\n".join(excerpts)

    for offer in proposal.get("offers") or []:
        cited = evidence_text(offer)
        if not _duration_is_grounded(offer.get("duration_minutes"), cited):
            raise ValueError("Erkannte Dauer ist nicht durch die FunctionGemma-Belege gedeckt")
        if not _amount_is_grounded(offer.get("amount_cents"), cited):
            raise ValueError("Erkannter Preis ist nicht durch die FunctionGemma-Belege gedeckt")
        if not _amount_is_grounded(offer.get("amount_max_cents"), cited):
            raise ValueError("Erkannter Maximalpreis ist nicht durch die FunctionGemma-Belege gedeckt")

    for rule in proposal.get("service_rules") or []:
        cited = evidence_text(rule)
        if not _service_label_is_grounded(rule.get("raw_label"), cited):
            raise ValueError("Erkannter Service ist nicht durch die FunctionGemma-Belege gedeckt")
        if not _amount_is_grounded(rule.get("amount_cents"), cited):
            raise ValueError("Erkannter Service-Aufpreis ist nicht durch die FunctionGemma-Belege gedeckt")

    for condition in proposal.get("conditions") or []:
        cited = evidence_text(condition)
        if not _amount_is_grounded(condition.get("amount_cents"), cited):
            raise ValueError("Erkannter Bedingungsbetrag ist nicht durch die FunctionGemma-Belege gedeckt")