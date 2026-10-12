from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import Iterable


@dataclass(frozen=True)
class ContextMessage:
    message_id: int
    direction: str
    message_type: str
    text: str
    timestamp: object
    role: str = "CONTEXT"
    ocr_text: str | None = None

    @property
    def effective_text(self) -> str:
        if self.message_type == "IMAGE" and self.ocr_text:
            return self.ocr_text
        return self.text or ""


@dataclass(frozen=True)
class PriceContext:
    thread_id: int
    person_id: int
    messages: tuple[ContextMessage, ...]
    fingerprint: str

    def as_text(self) -> str:
        lines = []
        for item in self.messages:
            body = item.effective_text.strip()
            if not body:
                body = "<IMAGE>" if item.message_type == "IMAGE" else "<EMPTY>"
            lines.append(f"[{item.message_id} {item.direction} {item.message_type}] {body}")
        return "\n".join(lines)


def build_context(*, thread_id: int, person_id: int, rows: Iterable[ContextMessage], seed_message_id: int | None = None,
                  max_messages: int = 12, max_chars: int = 4000, gap_minutes: int = 10) -> PriceContext:
    items = [x for x in rows if x.direction in {"IN", "OUT"} and x.message_type != "SYSTEM"]
    items.sort(key=lambda x: (x.timestamp, x.message_id))
    if not items:
        return _final(thread_id, person_id, [])
    seed_index = len(items) - 1
    if seed_message_id is not None:
        for i, item in enumerate(items):
            if item.message_id == seed_message_id:
                seed_index = i; break

    selected = [items[seed_index]]
    total_chars = len(items[seed_index].effective_text)
    i = seed_index - 1
    last_time = items[seed_index].timestamp
    while i >= 0 and len(selected) < max_messages and total_chars < max_chars:
        item = items[i]
        try:
            gap = last_time - item.timestamp
        except TypeError:
            gap = timedelta(0)
        allow_question = item.direction == "OUT" and any(k in item.effective_text.lower() for k in ("kost", "preis", "wie viel", "nimm"))
        if gap > timedelta(minutes=gap_minutes) and not allow_question:
            break
        selected.append(item)
        total_chars += len(item.effective_text)
        last_time = item.timestamp
        i -= 1
    selected.reverse()

    i = seed_index + 1
    last_time = items[seed_index].timestamp
    while i < len(items) and len(selected) < max_messages and total_chars < max_chars:
        item = items[i]
        try:
            gap = item.timestamp - last_time
        except TypeError:
            gap = timedelta(0)
        if gap > timedelta(minutes=gap_minutes):
            break
        if item.direction == "IN" or len(selected) < 3:
            selected.append(item)
            total_chars += len(item.effective_text)
            last_time = item.timestamp
        i += 1
    return _final(thread_id, person_id, selected)


def _final(thread_id: int, person_id: int, items: list[ContextMessage]) -> PriceContext:
    raw = "\n".join(f"{x.message_id}|{x.direction}|{x.message_type}|{x.effective_text}" for x in items)
    return PriceContext(thread_id=thread_id, person_id=person_id, messages=tuple(items), fingerprint=sha256(raw.encode("utf-8")).hexdigest())
