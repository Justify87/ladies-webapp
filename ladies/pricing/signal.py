from __future__ import annotations

import re
from dataclasses import dataclass

from .normalize import extract_durations, extract_money, fold

PRICE_WORD_RE = re.compile(r"\b(preis(?:e|liste)?|kost(?:et|en)?|nehme|nimmt|zahlen?|euro|eur)\b", re.I)
INCLUDE_RE = re.compile(r"\b(inkl\.?|inklusive|dabei|inbegriffen)\b", re.I)
SURCHARGE_RE = re.compile(r"(?:\+\s*\d{2,4}|\b(?:extra|aufpreis|zuschlag)\b)", re.I)
PRICE_QUESTION_RE = re.compile(r"\b(was\s+(?:kostet|nimmst|verlangst)|wie\s+viel|preis|preise)\b", re.I)


@dataclass(frozen=True)
class SignalResult:
    score: int
    reasons: tuple[str, ...]
    strong: bool


def detect_price_signal(text: str | None, *, previous_outgoing: str | None = None, has_image: bool = False) -> SignalResult:
    value = str(text or "")
    folded = fold(value)
    durations = extract_durations(value)
    monies = extract_money(value)
    score = 0
    reasons: list[str] = []

    if re.search(r"€|\beur\b|\beuro\b", value, re.I):
        score += 4; reasons.append("currency")
    if durations:
        score += 3; reasons.append("duration")
    explicit_money = [m for m in monies if m.get("explicit")]
    if explicit_money:
        score += 2; reasons.append("amount")
    if durations and monies:
        score += 5; reasons.append("duration_amount")
    if PRICE_WORD_RE.search(value):
        score += 2; reasons.append("price_word")
    if SURCHARGE_RE.search(value):
        score += 3; reasons.append("surcharge")
    if INCLUDE_RE.search(value):
        score += 2; reasons.append("included")
    if previous_outgoing and PRICE_QUESTION_RE.search(previous_outgoing):
        score += 4; reasons.append("price_question_context")
        if re.fullmatch(r"\s*\d{2,4}(?:[\.,]\d{1,2})?\s*", value):
            score += 3; reasons.append("bare_answer")
    if has_image:
        score += 1; reasons.append("image")

    if not (durations or PRICE_WORD_RE.search(value) or explicit_money or previous_outgoing):
        if re.search(r"\+?\d{8,}", folded):
            score = max(0, score - 4); reasons.append("phone_like")

    return SignalResult(score=score, reasons=tuple(dict.fromkeys(reasons)), strong=score >= 7)
