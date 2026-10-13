from __future__ import annotations

import re
from typing import Any

_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d .()/-]{7,}\d)(?!\w)")
_JID_RE = re.compile(r"\b[0-9A-Za-z_.:-]{5,}@(s\.whatsapp\.net|lid|g\.us)\b", re.IGNORECASE)
_SECRET_RE = re.compile(r"(?i)\b(token|secret|authorization|cookie|password)\b\s*[:=]\s*([^\s,;&]+)")


def redact_text(value: Any, *, limit: int = 2000) -> str:
    text = str(value or "")[:limit]
    text = _JID_RE.sub("[WHATSAPP_ADDRESS]", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    text = _SECRET_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    return text


def sanitize_log_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 2:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 20:
                out["…"] = "[TRUNCATED]"
                break
            key_text = str(key)[:80]
            if any(marker in key_text.lower() for marker in ("token", "secret", "password", "cookie", "authorization", "message", "text")):
                out[key_text] = "[REDACTED]"
            else:
                out[key_text] = sanitize_log_value(item, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [sanitize_log_value(item, depth=depth + 1) for item in list(value)[:20]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return redact_text(value, limit=500) if isinstance(value, str) else value
    return redact_text(repr(value), limit=500)
