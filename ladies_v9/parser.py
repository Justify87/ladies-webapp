from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from .domain import MediaReference, ProfileData, SearchProfile

LOGGER = logging.getLogger("ladies_explorer.parser")
from .utils import BASE, category_from_url, clean_text

A_IMAGE_RE = re.compile(
    r"^https://bilder\d+\.ladies-cdn\.de/Bilder/\d+/\d+-A(\d+)\.JPG(?:\?[^#\s\"'<>]*)?$",
    re.IGNORECASE,
)
F_IMAGE_RE = re.compile(
    r"^https://bilder\d+\.ladies-cdn\.de/Bilder/\d+/\d+-F(\d+)\.JPG(?:\?[^#\s\"'<>]*)?$",
    re.IGNORECASE,
)
VIDEO_RE = re.compile(
    r"https://video\d+\.ladies-cdn\.de/[^\s\"'<>]+?\.(?:webm|mp4)(?:\?[^\s\"'<>]*)?",
    re.IGNORECASE,
)
ADVERT_ID_RE = re.compile(r'advertId:\s*replaceWithDefault\("(\d+)"', re.IGNORECASE)
CANONICAL_ID_RE = re.compile(r'canonicalId:\s*replaceWithDefault\("([^\"]*)"', re.IGNORECASE)

SEARCH_CONFIG_PATTERNS = {
    "total_pages": re.compile(r'totalPages:\s*Number\.parseInt\(replaceWithDefault\("(-?\d+)"', re.I),
    "current_page": re.compile(r'currentPage:\s*Number\.parseInt\(replaceWithDefault\("(-?\d+)"', re.I),
    "advert_count": re.compile(r'advertCount:\s*Number\.parseInt\(replaceWithDefault\("(-?\d+)"', re.I),
    "distance_searched": re.compile(r'distanceSearched:\s*Number\.parseInt\(replaceWithDefault\("(-?\d+)"', re.I),
}


def _revision_token(url: str) -> str | None:
    try:
        return (parse_qs(urlparse(url).query).get("h") or [None])[0]
    except Exception:
        return None


def _base_url(url: str) -> str:
    p = urlparse(url)
    return p._replace(query="", fragment="").geturl()


def parse_search_config(raw_html: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, pattern in SEARCH_CONFIG_PATTERNS.items():
        match = pattern.search(raw_html)
        if match:
            try:
                out[key] = int(match.group(1))
            except ValueError:
                pass
    return out


def card_has_ai_content(card: BeautifulSoup) -> bool:
    markers = (
        "ki-kennzeichnung", "ki-generiert", "ki-bearbeitet", "ai-icon", "ai_icon",
        "basic-eu-ai", "eu-ai-icon", "artificial-intelligence", "artificial_intelligence",
    )
    html = str(card).lower()
    if any(marker in html for marker in markers):
        return True
    for el in card.find_all(True):
        attrs = " ".join(
            f"{k}={v}" for k, v in el.attrs.items()
            if k in {"class", "id", "title", "aria-label", "alt", "href", "src", "data-icon", "data-type"}
        ).lower()
        if any(marker in attrs for marker in markers):
            return True
    return False


def parse_search_cards(fragment: str) -> list[SearchProfile]:
    soup = BeautifulSoup(fragment, "html.parser")
    out: list[SearchProfile] = []
    for card in soup.select("div.ladies-card"):
        link = card.select_one("a.card-link[data-ref-id]")
        if not link:
            continue
        advert_id = clean_text(link.get("data-ref-id"))
        href = clean_text(link.get("href"))
        if not advert_id or not href:
            continue
        profile_url = urljoin(BASE, href)
        name_el = card.select_one(".card-name")
        city_el = card.select_one(".card-city")
        img_el = card.select_one("img.card-thumb-image")
        classes = set(card.get("class") or [])
        markers = {
            "has_18plus": bool(card.select_one('[class*="18"], [title*="18"]')),
            "has_stars": bool(card.select_one('[class*="star"], [title*="Star"]')),
        }
        out.append(SearchProfile(
            advert_id=advert_id,
            name=clean_text(name_el.get_text(" ", strip=True) if name_el else ""),
            city=clean_text(city_el.get_text(" ", strip=True) if city_el else ""),
            category=category_from_url(profile_url),
            profile_url=profile_url,
            thumbnail_url=clean_text(img_el.get("src")) if img_el and img_el.get("src") else None,
            has_video=bool(card.select_one(".card-videos-marker")),
            is_premium="card-gallery-premium" in classes,
            has_ai_content=card_has_ai_content(card),
            markers=markers,
        ))
    return out


def extract_traits(soup: BeautifulSoup) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    root = soup.select_one(".trait-attributes")
    if not root:
        return out
    for item in root.select(".attribute-items"):
        label = item.select_one("strong")
        value_col = item.select_one(".col-xs-7")
        if not label or not value_col:
            continue
        key = clean_text(label.get_text(" ", strip=True))
        vals = []
        for x in value_col.select("p"):
            v = clean_text(x.get_text(" ", strip=True))
            if v and v not in vals:
                vals.append(v)
        if key and vals:
            out[key] = vals
    return out


def extract_services(soup: BeautifulSoup) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    root = soup.select_one(".standard-attributes")
    if not root:
        return out
    for group in root.select(".list-group"):
        heading = group.select_one(".attribute-heading strong")
        if not heading:
            continue
        key = clean_text(heading.get_text(" ", strip=True))
        vals = list(dict.fromkeys(
            clean_text(x.get_text(" ", strip=True)) for x in group.select(".attribute-item span")
        ))
        vals = [v for v in vals if v]
        if key and vals:
            out[key] = vals
    return out


def extract_opening_times(soup: BeautifulSoup) -> dict[str, str]:
    out: dict[str, str] = {}
    for box in soup.select(".opening-times"):
        heading = box.select_one(".attribute-heading strong")
        if not heading:
            continue
        day = clean_text(heading.get_text(" ", strip=True))
        full = clean_text(box.get_text(" ", strip=True))
        value = clean_text(full.replace(day, "", 1))
        if day and value:
            out[day] = value
    if out:
        return out
    # Fallback to profile-bound microdata, still higher confidence than generic JSON-LD.
    day_map = {
        "Monday":"Montag", "Tuesday":"Dienstag", "Wednesday":"Mittwoch", "Thursday":"Donnerstag",
        "Friday":"Freitag", "Saturday":"Samstag", "Sunday":"Sonntag",
    }
    for spec in soup.select('[itemprop="openingHoursSpecification"]'):
        link = spec.select_one('[itemprop="dayOfWeek"]')
        desc = spec.select_one('[itemprop="description"]')
        if not link or not desc:
            continue
        day = (link.get("href") or "").rstrip("/").split("/")[-1]
        value = clean_text(desc.get("content") or desc.get_text(" ", strip=True))
        if day and value:
            out[day_map.get(day, day)] = value
    return out


def extract_contact_data(soup: BeautifulSoup) -> dict[str, str | None]:
    phone_display = None
    whatsapp_url = None
    sms_url = None
    tel_url = None
    phone_el = soup.select_one('[itemprop="telephone"]')
    if phone_el:
        phone_display = clean_text(phone_el.get("content") or phone_el.get_text(" ", strip=True)) or None
    for a in soup.find_all("a", href=True):
        href = clean_text(a.get("href"))
        lower = href.lower()
        if not whatsapp_url and (lower.startswith("https://wa.me/") or lower.startswith("http://wa.me/")):
            whatsapp_url = href
        if not sms_url and lower.startswith("sms:"):
            sms_url = href
        if not tel_url and lower.startswith("tel:"):
            tel_url = href
    if phone_display and not tel_url:
        dial = re.sub(r"[^0-9+]", "", phone_display)
        if dial:
            tel_url = f"tel:{dial}"
    return {"phone_display": phone_display, "whatsapp_url": whatsapp_url, "sms_url": sms_url, "tel_url": tel_url}


def extract_profile_published_at(soup: BeautifulSoup) -> str | None:
    for meta in soup.select('meta[property="article:published_time"]'):
        value = clean_text(meta.get("content"))
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if 2000 <= parsed.year <= 2100:
            return parsed.isoformat()
    return None


def extract_source_profile_id(soup: BeautifulSoup) -> str | None:
    el = soup.select_one("[data-profile-id]")
    value = clean_text(el.get("data-profile-id")) if el else ""
    return value or None


def extract_postal_code(soup: BeautifulSoup) -> str | None:
    el = soup.select_one('[itemprop="postalCode"]')
    if not el:
        return None
    return clean_text(el.get("content") or el.get_text(" ", strip=True)) or None


def extract_hidden_location(soup: BeautifulSoup) -> str | None:
    el = soup.select_one(".address-details[data-ort]") or soup.select_one("[data-ort]")
    value = clean_text(el.get("data-ort")) if el else ""
    return value or None


def extract_json_ld(soup: BeautifulSoup) -> list[dict]:
    out = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except Exception:
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def extract_media(soup: BeautifulSoup, raw_html: str) -> list[MediaReference]:
    """Extract gallery media in the order Ladies actually presents it.

    The site exposes a stable ``data-index`` across the main image and gallery
    items. Videos can sit between photos (for example A1 -> video -> A2), so
    sorting IMAGE and VIDEO independently loses the real gallery sequence.
    """
    candidates: set[str] = set()
    for img in soup.find_all("img"):
        src = img.get("src")
        if isinstance(src, str):
            candidates.add(urljoin(BASE, src.strip().rstrip("),;")))
    for tag in soup.find_all(style=True):
        style = tag.get("style") or ""
        for candidate in re.findall(r"url\(([^)]+)\)", style, flags=re.I):
            candidates.add(urljoin(BASE, candidate.strip(" \"'").rstrip("),;")))
    candidates.update(re.findall(
        r"https://bilder\d+\.ladies-cdn\.de/Bilder/\d+/\d+-(?:A|F)\d+\.JPG(?:\?[^\"'<>\s)]*)?",
        raw_html, re.I,
    ))

    # Build the best photo URL for each photo number. Prefer A (full-size)
    # over F (thumbnail/gallery) variants.
    by_photo_no: dict[int, tuple[str, str]] = {}
    for url in candidates:
        ma, mf = A_IMAGE_RE.match(url), F_IMAGE_RE.match(url)
        if ma:
            by_photo_no[int(ma.group(1))] = (url, "A")
        elif mf:
            num = int(mf.group(1))
            by_photo_no.setdefault(num, (url, "F"))

    ordered: list[MediaReference] = []
    used_urls: set[str] = set()
    used_photo_numbers: set[int] = set()

    def add_image(url: str | None, position: int) -> None:
        if not url:
            return
        full = urljoin(BASE, url.strip().rstrip("),;"))
        ma, mf = A_IMAGE_RE.match(full), F_IMAGE_RE.match(full)
        if not (ma or mf):
            return
        photo_no = int((ma or mf).group(1))
        best_url, variant = by_photo_no.get(photo_no, (full, "A" if ma else "F"))
        if best_url in used_urls or photo_no in used_photo_numbers:
            return
        used_urls.add(best_url)
        used_photo_numbers.add(photo_no)
        ordered.append(MediaReference(
            url=best_url, kind="IMAGE", position=position, variant=variant,
            revision_token=_revision_token(best_url),
            role="PRIMARY" if photo_no == 1 else "GALLERY",
        ))

    def add_video(url: str | None, position: int) -> None:
        if not url:
            return
        full = urljoin(BASE, url.strip().rstrip("),;"))
        if not VIDEO_RE.fullmatch(full):
            return
        if full in used_urls:
            return
        used_urls.add(full)
        ordered.append(MediaReference(
            url=full, kind="VIDEO", position=position,
            variant=urlparse(full).path.rsplit(".", 1)[-1].lower(),
            revision_token=_revision_token(full), role="GALLERY",
        ))

    # Main photo is data-index 0 on current Ladies markup.
    main = soup.select_one(".auftrag-main-thumb[data-index]")
    if main:
        try:
            position = int(main.get("data-index")) + 1
        except (TypeError, ValueError):
            position = 1
        img = main.find("img")
        add_image(img.get("src") if img else None, position)

    # The gallery's data-index describes the mixed photo/video order.
    for item in soup.select(".girl-galerie .gallery_item[data-index]"):
        try:
            position = int(item.get("data-index")) + 1
        except (TypeError, ValueError):
            continue
        href = item.get("href")
        classes = set(item.get("class") or [])
        if "gallery_video" in classes or (
            isinstance(href, str)
            and VIDEO_RE.fullmatch(urljoin(BASE, href.strip()))
        ):
            add_video(href, position)
        else:
            add_image(href, position)

    # Fallback for layouts without data-index: preserve photo number order and
    # append any videos that were only discoverable from raw HTML.
    next_pos = max((m.position for m in ordered), default=0) + 1
    for photo_no, (url, _variant) in sorted(by_photo_no.items()):
        if photo_no in used_photo_numbers:
            continue
        add_image(url, next_pos)
        next_pos += 1
    for url in dict.fromkeys(v.rstrip("),;") for v in VIDEO_RE.findall(raw_html)):
        if url in used_urls:
            continue
        add_video(url, next_pos)
        next_pos += 1

    return sorted(ordered, key=lambda m: (m.position, m.kind))


def parse_profile_html(raw_html: str, source_url: str, fallback: SearchProfile) -> ProfileData:
    soup = BeautifulSoup(raw_html, "html.parser")
    advert_m = ADVERT_ID_RE.search(raw_html)
    canonical_m = CANONICAL_ID_RE.search(raw_html)
    name_el = soup.select_one("h2.page-heading.kuenstlername") or soup.select_one(".share-advert-name strong")
    city_el = soup.select_one(".address-details [itemprop='addressLocality'] .important") or soup.select_one(".share-advert-place")
    desc_el = soup.select_one(".einzelansicht-text > div:first-child")
    contact = extract_contact_data(soup)
    json_ld = extract_json_ld(soup)
    visible_city = clean_text(city_el.get_text(" ", strip=True) if city_el else fallback.city).removeprefix("in ")
    source_profile_id = extract_source_profile_id(soup)
    canonical = canonical_m.group(1) if canonical_m and canonical_m.group(1) else None

    auxiliary = {
        "json_ld": json_ld,
        "hidden_location": extract_hidden_location(soup),
    }
    provenance = {
        "name": {"source":"DOM_VISIBLE","rank":100},
        "city": {"source":"DOM_VISIBLE","rank":100},
        "postal_code": {"source":"MICRODATA","rank":90},
        "source_profile_id": {"source":"SITE_INTERNAL_ID","rank":95},
        "canonical_id": {"source":"SITE_INTERNAL_ID","rank":95},
        "hidden_location": {"source":"DATA_ATTRIBUTE","rank":40},
        "json_ld": {"source":"JSON_LD","rank":30},
    }
    name = clean_text(name_el.get_text(" ", strip=True) if name_el else fallback.name)
    media = extract_media(soup, raw_html)

    LOGGER.debug("Parsed profile advert_id=%s name=%s city=%s media=%s", fallback.advert_id, name, visible_city, len(media))
    return ProfileData(
        advert_id=advert_m.group(1) if advert_m else fallback.advert_id,
        source_profile_id=source_profile_id,
        canonical_id=canonical,
        name=name,
        city=visible_city,
        postal_code=extract_postal_code(soup),
        hidden_location=extract_hidden_location(soup),
        category=fallback.category,
        source_url=source_url,
        description=clean_text(desc_el.get_text(" ", strip=True) if desc_el else ""),
        phone_display=contact["phone_display"],
        whatsapp_url=contact["whatsapp_url"],
        sms_url=contact["sms_url"],
        tel_url=contact["tel_url"],
        traits=extract_traits(soup),
        services=extract_services(soup),
        opening_times=extract_opening_times(soup),
        published_at=extract_profile_published_at(soup),
        media=media,
        provenance=provenance,
        auxiliary=auxiliary,
        fetched_at=time.time(),
    )
