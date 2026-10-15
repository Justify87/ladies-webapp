from __future__ import annotations

from dataclasses import dataclass
import unicodedata
from typing import Iterable

from .schema import PRICE_DURATION_OPTIONS


PRICE_WORDS = {
    "preis", "preise", "preisliste", "kostet", "kosten", "kost", "nehme", "nimm", "nimmst",
    "verlange", "verlangst", "verlangt", "euro", "eur", "aufpreis", "zuschlag", "extra",
}
PRICE_QUESTION_PHRASES = (
    "was kostet", "was kosten", "wie viel", "welcher preis", "welche preise", "was nimmst", "was verlangst",
)
DURATION_UNITS = {
    "min", "mins", "minute", "minuten", "h", "std", "std.", "stunde", "stunden",
}
STATUS_WORDS = {
    "inkl", "inkl.", "inklusive", "dabei", "inbegriffen", "extra", "aufpreis", "zuschlag", "ohne",
    "nicht", "ausgeschlossen", "anfrage", "absprache", "wunsch", "verhandelbar", "vb",
}
CURRENCY_WORDS = {"eur", "euro", "€"}
DURATION_PHRASES = (
    "halbe stunde", "halbe std", "eine stunde", "eine std", "ne stunde", "ne std", "zwei stunden", "zwei std",
)


@dataclass(frozen=True)
class DetectionResult:
    is_price_message: bool
    reasons: tuple[str, ...]


def _plain(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    chars: list[str] = []
    previous_space = False
    for ch in text:
        if ch.isalnum() or ch in {"€", "+", ",", ".", "-"}:
            chars.append(ch)
            previous_space = False
        elif not previous_space:
            chars.append(" ")
            previous_space = True
    return " ".join("".join(chars).split())


def _tokens(value: str | None) -> tuple[str, ...]:
    text = _plain(value)
    return tuple(text.split()) if text else ()


def _number_value(token: str) -> float | None:
    value = token.strip().strip("€+").rstrip(".,-")
    if not value:
        return None
    value = value.replace(",", ".")
    if value.count(".") > 1:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _combined_duration(token: str) -> tuple[float, str] | None:
    for unit in sorted(DURATION_UNITS, key=len, reverse=True):
        if not token.endswith(unit) or token == unit:
            continue
        number = _number_value(token[: -len(unit)])
        if number is not None:
            return number, unit
    return None


def _number_tokens(tokens: tuple[str, ...]) -> list[float]:
    values: list[float] = []
    for token in tokens:
        combined = _combined_duration(token)
        if combined is not None:
            values.append(combined[0])
            continue
        value = _number_value(token)
        if value is not None:
            values.append(value)
    return values


def _duration_numbers(tokens: tuple[str, ...]) -> list[float]:
    values: list[float] = []
    for index, token in enumerate(tokens):
        combined = _combined_duration(token)
        if combined is not None:
            values.append(combined[0])
            continue
        if token not in DURATION_UNITS or index == 0:
            continue
        value = _number_value(tokens[index - 1])
        if value is not None:
            values.append(value)
    text = " ".join(tokens)
    if "halbe stunde" in text or "halbe std" in text or "eine halbe stunde" in text:
        values.append(0.5)
    if "eine stunde" in text or "eine std" in text or "1 stunde" in text:
        values.append(1.0)
    return values


def _has_currency(tokens: tuple[str, ...]) -> bool:
    return any(token in CURRENCY_WORDS or "€" in token for token in tokens)


def _has_price_word(tokens: tuple[str, ...]) -> bool:
    return any(token in PRICE_WORDS for token in tokens)


def _has_status_word(tokens: tuple[str, ...]) -> bool:
    return any(token in STATUS_WORDS for token in tokens)


def _has_plus_amount(tokens: tuple[str, ...]) -> bool:
    return any(token.startswith("+") and _number_value(token) is not None for token in tokens)


def _has_duration_phrase(text: str) -> bool:
    return any(phrase in text for phrase in DURATION_PHRASES)


def _plausible_price_values(numbers: list[float]) -> list[float]:
    durations = set(PRICE_DURATION_OPTIONS)
    return [value for value in numbers if 40 <= value <= 5000 and value not in durations]


def _looks_like_numeric_price_table(tokens: tuple[str, ...], numbers: list[float]) -> bool:
    if len(tokens) > 12 or len(numbers) < 2:
        return False
    durations = set(PRICE_DURATION_OPTIONS)
    has_duration_value = any(value in durations for value in numbers)
    return has_duration_value and bool(_plausible_price_values(numbers))


def _has_service(text: str, service_terms: Iterable[str]) -> bool:
    if not text:
        return False
    padded = f" {text} "
    for term in service_terms:
        normalized = _plain(term)
        if normalized and f" {normalized} " in padded:
            return True
    return False


def _is_price_question(value: str | None) -> bool:
    text = _plain(value)
    if not text:
        return False
    return any(phrase in text for phrase in PRICE_QUESTION_PHRASES) or any(word in text.split() for word in PRICE_WORDS)


class PriceMessageDetector:
    """High-recall, no-training classifier for selecting messages for FunctionGemma.

    The detector never extracts or assigns prices. It only decides whether an
    incoming message is likely price-related. Ambiguous messages are deliberately
    delegated to FunctionGemma instead of being interpreted here.
    """

    name = "token-price-detector"
    version = "4"

    def __init__(self, service_terms: Iterable[str] = ()) -> None:
        self.service_terms = tuple(dict.fromkeys(str(term) for term in service_terms if str(term).strip()))

    def detect(
        self,
        text: str | None,
        *,
        previous_outgoing: str | None = None,
        has_image: bool = False,
    ) -> DetectionResult:
        normalized = _plain(text)
        tokens = _tokens(text)
        numbers = _number_tokens(tokens)
        durations = _duration_numbers(tokens)
        has_currency = _has_currency(tokens)
        has_price_word = _has_price_word(tokens)
        has_status_word = _has_status_word(tokens)
        has_service = _has_service(normalized, self.service_terms)
        has_plus_amount = _has_plus_amount(tokens)
        has_duration_phrase = _has_duration_phrase(normalized)
        numeric_price_table = _looks_like_numeric_price_table(tokens, numbers)
        question_context = _is_price_question(previous_outgoing)
        malformed_duration = any(value > 240 for value in durations)
        plausible_prices = _plausible_price_values(numbers)

        reasons: list[str] = []
        if has_currency:
            reasons.append("currency")
        if has_price_word:
            reasons.append("price_word")
        if durations or has_duration_phrase:
            reasons.append("duration")
        if has_service:
            reasons.append("service")
        if has_status_word:
            reasons.append("service_status")
        if has_plus_amount:
            reasons.append("surcharge_amount")
        if numeric_price_table:
            reasons.append("numeric_price_table")
        if question_context:
            reasons.append("price_question_context")
        if malformed_duration:
            reasons.append("compact_or_malformed_price_row")
        if has_image:
            reasons.append("image")

        relevant = False
        if has_currency and numbers:
            relevant = True
        elif has_price_word and numbers:
            relevant = True
        elif numeric_price_table:
            relevant = True
        elif has_duration_phrase and plausible_prices:
            relevant = True
        elif malformed_duration:
            relevant = True
        elif question_context and (numbers or has_service or has_status_word):
            relevant = True
        elif has_service and (has_status_word or has_plus_amount):
            relevant = True
        elif has_image and (has_currency or has_price_word or numeric_price_table):
            relevant = True

        return DetectionResult(is_price_message=relevant, reasons=tuple(dict.fromkeys(reasons)))