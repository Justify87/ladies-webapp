from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from .errors import ValidationError

T = TypeVar("T")


def read_int(
    source: Mapping[str, Any],
    key: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = source.get(key, default)
    if raw is None or raw == "":
        if default is None:
            raise ValidationError(f"{key} fehlt")
        raw = default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{key} muss eine ganze Zahl sein") from exc
    if minimum is not None and value < minimum:
        raise ValidationError(f"{key} muss mindestens {minimum} sein")
    if maximum is not None and value > maximum:
        raise ValidationError(f"{key} darf höchstens {maximum} sein")
    return value


def read_bool(source: Mapping[str, Any], key: str, *, default: bool = False) -> bool:
    raw = source.get(key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and raw in {0, 1}:
        return bool(raw)
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise ValidationError(f"{key} muss true oder false sein")


def read_enum(source: Mapping[str, Any], key: str, allowed: set[str], *, default: str | None = None) -> str:
    raw = source.get(key, default)
    if raw is None:
        raise ValidationError(f"{key} fehlt")
    value = str(raw).strip().upper()
    normalized = {str(item).upper() for item in allowed}
    if value not in normalized:
        raise ValidationError(f"{key} hat einen ungültigen Wert")
    return value


def read_mapping(source: Mapping[str, Any], key: str) -> dict[str, Any]:
    raw = source.get(key)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValidationError(f"{key} muss ein Objekt sein")
    return dict(raw)
