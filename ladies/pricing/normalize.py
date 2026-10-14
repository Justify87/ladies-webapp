from __future__ import annotations

import re
import unicodedata
from typing import Any

SPACE_RE = re.compile(r"\s+")
DURATION_PATTERNS = [
    (re.compile(r"(?<!\d)(\d{1,3})\s*(?:min(?:ute[n]?)?|mins?)\b", re.I), lambda m: int(m.group(1))),
    (re.compile(r"(?<!\d)(\d+(?:[\.,]\d+)?)\s*(?:h|std\.?|stunde[n]?)\b", re.I), lambda m: round(float(m.group(1).replace(",", ".")) * 60)),
    (re.compile(r"\b(?:eine\s+)?halbe\s+stunde\b|½\s*(?:h|std\.?)", re.I), lambda m: 30),
    (re.compile(r"(?<!\d)\d{2,4}(15|20|30|45|60|90|120|180)\s*(?:min(?:ute[n]?)?|mins?)\b", re.I), lambda m: int(m.group(1))),
    (re.compile(r"(?<!\d)\d{2,4}\s*(?:h|std\.?)\b", re.I), lambda m: 60),
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

_PRICE_FIRST_MINUTES_RE = re.compile(
    r"^\s*(?P<price>\d{2,4})(?:\s*(?:€|eur|euro))?\s+(?P<duration>\d{1,3})\s*(?:min(?:ute[n]?)?|mins?)\b",
    re.I,
)
_PRICE_FIRST_HOURS_RE = re.compile(
    r"^\s*(?P<price>\d{2,4})(?:\s*(?:€|eur|euro))?\s+(?P<hours>\d+(?:[\.,]\d+)?)\s*(?:h|std\.?|stunde[n]?)\b",
    re.I,
)
_PRICE_PER_HOUR_SHORTHAND_RE = re.compile(
    r"^\s*(?P<price>\d{2,4})(?:\s*(?:€|eur|euro))?\s*(?:h|std\.?|stunde[n]?)\s*$",
    re.I,
)
_DURATION_FIRST_MINUTES_RE = re.compile(
    r"^\s*(?P<duration>\d{1,3})\s*(?:min(?:ute[n]?)?|mins?)\s+(?P<price>\d{2,4})(?:\s*(?:€|eur|euro))?\b",
    re.I,
)
_DURATION_FIRST_HOURS_RE = re.compile(
    r"^\s*(?P<hours>\d+(?:[\.,]\d+)?)\s*(?:h|std\.?|stunde[n]?)\s+(?P<price>\d{2,4})(?:\s*(?:€|eur|euro))?\b",
    re.I,
)
_COMPACT_PRICE_MINUTES_RE = re.compile(
    r"^\s*(?P<combined>\d{4,7})\s*(?:min(?:ute[n]?)?|mins?)\b",
    re.I,
)
_COMMON_DURATIONS = {15, 20, 30, 45, 60, 90, 120, 180}

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


def extract_price_duration_pairs(text: str | None) -> list[dict[str, Any]]:
    """Extract line-oriented price rows that are common in chat price lists.

    Besides normal forms such as ``50 20 Minuten`` this deliberately handles
    compact typing like ``10045 Minuten`` (100 EUR / 45 min) and the common
    shorthand ``130 Std`` (130 EUR / one hour). Conservative amount and
    duration bounds keep ordinary duration-only chat text from becoming a
    price row accidentally.
    """
    value = unicodedata.normalize("NFKC", str(text or "")).replace("\u00a0", " ")
    out: list[dict[str, Any]] = []
    for line_match in re.finditer(r"[^\r\n]+", value):
        line = normalize_text(line_match.group(0))
        if not line:
            continue
        pair = _parse_price_duration_line(line)
        if pair is None:
            continue
        price, minutes = pair
        if not (20 <= price <= 5000 and 5 <= minutes <= 1440):
            continue
        out.append({
            "duration_minutes": minutes,
            "amount_cents": price * 100,
            "start": line_match.start(),
            "end": line_match.end(),
            "raw": line,
        })
    seen: set[tuple[int, int]] = set()
    unique = []
    for row in out:
        key = (row["duration_minutes"], row["amount_cents"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def _parse_price_duration_line(line: str) -> tuple[int, int] | None:
    match = _PRICE_FIRST_MINUTES_RE.search(line)
    if match:
        return int(match.group("price")), int(match.group("duration"))

    match = _PRICE_FIRST_HOURS_RE.search(line)
    if match:
        hours = float(match.group("hours").replace(",", "."))
        return int(match.group("price")), round(hours * 60)

    match = _DURATION_FIRST_MINUTES_RE.search(line)
    if match:
        return int(match.group("price")), int(match.group("duration"))

    match = _DURATION_FIRST_HOURS_RE.search(line)
    if match:
        hours = float(match.group("hours").replace(",", "."))
        return int(match.group("price")), round(hours * 60)

    match = _PRICE_PER_HOUR_SHORTHAND_RE.search(line)
    if match:
        return int(match.group("price")), 60

    match = _COMPACT_PRICE_MINUTES_RE.search(line)
    if not match:
        return None
    digits = match.group("combined")
    candidates: list[tuple[int, int, int]] = []
    for split in range(2, min(4, len(digits) - 1) + 1):
        price = int(digits[:split])
        minutes = int(digits[split:])
        if not (20 <= price <= 5000 and 5 <= minutes <= 240):
            continue
        score = 0
        if minutes in _COMMON_DURATIONS:
            score += 5
        if 15 <= minutes <= 120:
            score += 2
        if 40 <= price <= 500:
            score += 2
        if len(digits[:split]) in (2, 3):
            score += 1
        candidates.append((score, price, minutes))
    if not candidates:
        return None
    _, price, minutes = max(candidates, key=lambda row: (row[0], -abs(row[2] - 60), -row[1]))
    return price, minutes


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
    for pair in extract_price_duration_pairs(text):
        out.append({
            "amount_cents": pair["amount_cents"],
            "amount_max_cents": None,
            "amount_type": "EXACT",
            "start": pair["start"], "end": pair["end"], "raw": pair["raw"],
            "explicit": True,
            "is_surcharge": False,
            "structured_pair": True,
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
