from __future__ import annotations

import re
import unicodedata
from typing import Any

SPACE_RE = re.compile(r"\s+")
DURATION_PATTERNS = [
    (re.compile(r"(?<!\d)(\d{1,3})\s*(?:min(?:ute[n]?)?|mins?)\b", re.I), lambda m: int(m.group(1))),
    (re.compile(r"(?<!\d)(\d+(?:[\.,]\d+)?)\s*(?:h|std\.?|stunde[n]?)\b", re.I), lambda m: round(float(m.group(1).replace(",", ".")) * 60)),
    (re.compile(r"\b(?:eine\s+)?halbe\s+stunde\b|½\s*(?:h|std\.?)", re.I), lambda m: 30),
]
DURATION_RANGE_RE = re.compile(
    r"(?<!\d)\d{1,3}\s*[-–]\s*\d{1,3}\s*(?:min(?:ute[n]?)?|mins?|h|std\.?|stunde[n]?)\b",
    re.I,
)
MONEY_RE = re.compile(
    r"(?<!\d)(?P<prefix>\+|ab\s+|bis\s+|ca\.?\s*)?"
    r"(?P<amount>\d{2,4}(?:[\.,]\d{1,2})?)"
    r"\s*(?P<currency>€|eur\b|euro\b|,-\b|-\b)?",
    re.I,
)
RANGE_RE = re.compile(r"(?<!\d)(\d{2,4})\s*(?:€\s*)?[-–]\s*(\d{2,4})\s*(?:€|eur|euro)?", re.I)

SERVICE_ALIASES = {
    "KISSING": {"küssen", "kussen", "kusse", "küsse", "kuesse", "kissing", "zk"},
    "FRENCH": {"französisch", "franzosisch", "franzoesisch", "franz", "franz."},
    "ANAL": {"anal", "av"},
    "MASSAGE": {"massage", "body to body", "body-to-body"},
    "GFE": {"girlfriend", "girlfriend-sex", "gfe"},
    "SIXTY_NINE": {"69"},
}
SERVICE_LOOKUP = {alias: key for key, aliases in SERVICE_ALIASES.items() for alias in aliases}


def normalize_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("\u00a0", " ")
    return SPACE_RE.sub(" ", text).strip()


def fold(value: str | None) -> str:
    text = normalize_text(value).lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def extract_durations(text: str) -> list[dict[str, Any]]:
    out = []
    for pattern, convert in DURATION_PATTERNS:
        for match in pattern.finditer(text):
            try:
                minutes = int(convert(match))
            except (TypeError, ValueError):
                continue
            if 1 <= minutes <= 1440:
                out.append({"minutes": minutes, "start": match.start(), "end": match.end(), "raw": match.group(0)})
    return sorted(_dedupe_spans(out), key=lambda x: x["start"])


def extract_money(text: str) -> list[dict[str, Any]]:
    ranges = []
    occupied: list[tuple[int, int]] = [(m.start(), m.end()) for m in DURATION_RANGE_RE.finditer(text)]
    for match in RANGE_RE.finditer(text):
        if any(a <= match.start() < b or a < match.end() <= b for a, b in occupied):
            continue
        low, high = int(match.group(1)), int(match.group(2))
        if 0 < low <= high <= 5000:
            ranges.append({
                "amount_cents": low * 100,
                "amount_max_cents": high * 100,
                "amount_type": "RANGE",
                "start": match.start(), "end": match.end(), "raw": match.group(0),
            })
            occupied.append((match.start(), match.end()))
    out = list(ranges)
    folded = fold(text)
    price_context = any(token in folded for token in ("€", "eur", "euro", "preis", "kost", "nehme", "extra", "aufpreis", "ab "))
    durations = extract_durations(text)
    for match in MONEY_RE.finditer(text):
        if any(a <= match.start() < b for a, b in occupied):
            continue
        raw_amount = match.group("amount").replace(",", ".")
        try:
            amount = float(raw_amount)
        except ValueError:
            continue
        if any(d["start"] == match.start() and d["end"] >= match.end() for d in durations) and not match.group("currency"):
            continue
        if amount < 10 or amount > 5000:
            continue
        prefix = fold(match.group("prefix") or "")
        amount_type = "FROM" if "ab" in prefix else "UP_TO" if "bis" in prefix else "EXACT"
        currency = match.group("currency")
        explicit = bool(currency or price_context or prefix.strip().startswith("+"))
        out.append({
            "amount_cents": int(round(amount * 100)),
            "amount_max_cents": None,
            "amount_type": amount_type,
            "start": match.start(), "end": match.end(), "raw": match.group(0),
            "explicit": explicit,
            "is_surcharge": prefix.strip().startswith("+") or "extra" in folded[max(0, match.start()-20):match.end()+20] or "aufpreis" in folded[max(0, match.start()-20):match.end()+20],
        })
    return sorted(out, key=lambda x: x["start"])


def canonical_service(text: str) -> tuple[str | None, str | None]:
    f = fold(text)
    hits = []
    for alias, key in SERVICE_LOOKUP.items():
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", f):
            hits.append((f.find(alias), key, alias))
    if not hits:
        return None, None
    _, key, alias = sorted(hits)[0]
    return key, alias


def _dedupe_spans(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for row in rows:
        key = (row["start"], row["end"], row.get("minutes"))
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out
