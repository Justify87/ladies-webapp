from __future__ import annotations

import re
from datetime import datetime, timezone
import logging
from urllib.parse import urljoin, urlparse

LOGGER = logging.getLogger("ladies_explorer.utils")

BASE = "https://www.ladies.de"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def fold_location(value: str | None) -> str:
    value = clean_text(value).casefold().removeprefix("in ")
    return re.sub(r"[^a-z0-9äöüß]+", " ", value).strip()


def category_from_url(url: str) -> str:
    try:
        path = urlparse(url).path.strip("/").split("/")
        if len(path) >= 2 and path[0] == "sex-anzeigen":
            return path[1].replace("-", " ").title()
        return path[0].replace("-", " ").title() if path else ""
    except Exception:
        return ""


def slugify_location_input(value: str) -> str:
    value = clean_text(value).lower()
    for old, new in {"ä":"ae","ö":"oe","ü":"ue","ß":"ss","/":"-",",":"-"}.items():
        value = value.replace(old, new)
    value = re.sub(r"[^a-z0-9\- ]+", "", value).replace(" ", "-")
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "leipzig"


def safe_profile_url(value: str) -> str | None:
    value = clean_text(value)
    if not value:
        return None
    parsed = urlparse(urljoin(BASE, value))
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.hostname not in {"www.ladies.de", "m.ladies.de"}:
        return None
    if not parsed.path.startswith("/sex-anzeigen/"):
        return None
    return f"https://www.ladies.de{parsed.path}"


def safe_media_url(value: str) -> str | None:
    value = clean_text(value)
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except Exception:
        return None
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        return None
    if not (re.fullmatch(r"bilder\d+\.ladies-cdn\.de", hostname) or re.fullmatch(r"video\d+\.ladies-cdn\.de", hostname)):
        return None
    return parsed._replace(fragment="").geturl()


def parse_iso_datetime(value: str | None) -> datetime | None:
    value = clean_text(value)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None
