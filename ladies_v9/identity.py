from __future__ import annotations

import logging
import re
from dataclasses import dataclass

VERY_STRONG_IMAGE_DISTANCE = 3
STRONG_IMAGE_DISTANCE = 5
CANDIDATE_IMAGE_DISTANCE = 8

LOGGER = logging.getLogger("ladies_explorer.identity")


def normalize_phone_number(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip()
    if value.lower().startswith(("tel:", "sms:")):
        value = value.split(":", 1)[1]
    if "wa.me/" in value.lower():
        value = value.rstrip("/").split("/")[-1]
    digits = re.sub(r"\D", "", value)
    if not digits:
        return None
    if digits.startswith("0049"):
        digits = "49" + digits[4:]
    elif digits.startswith("0"):
        digits = "49" + digits[1:]
    elif not digits.startswith("49") and 7 <= len(digits) <= 15:
        # Do not invent a country code for arbitrary international numbers.
        return digits
    return "+" + digits


def dhash_distance(a: int, b: int) -> int:
    return int(a ^ b).bit_count()


def hash_to_hex(value: int) -> str:
    return f"{int(value) & ((1 << 64)-1):016x}"


def hash_from_hex(value: str) -> int:
    return int(value, 16)


@dataclass(frozen=True)
class MatchResult:
    decision: str
    reason: str
    best_distance: int | None = None
    candidate_image_matches: int = 0
    strong_image_matches: int = 0
    very_strong_image_matches: int = 0
    phone_match: bool = False
    source_profile_match: bool = False


def compare_hash_sets(old_hashes: list[int], new_hashes: list[int]) -> tuple[int | None, int, int, int]:
    if not old_hashes or not new_hashes:
        return None, 0, 0, 0
    distances = []
    matched_old: set[int] = set()
    for nh in new_hashes:
        best = min((dhash_distance(nh, oh), i) for i, oh in enumerate(old_hashes))
        distances.append(best[0])
        if best[0] <= CANDIDATE_IMAGE_DISTANCE:
            matched_old.add(best[1])
    best_distance = min(distances) if distances else None
    candidate = sum(1 for d in distances if d <= CANDIDATE_IMAGE_DISTANCE)
    strong = sum(1 for d in distances if d <= STRONG_IMAGE_DISTANCE)
    very = sum(1 for d in distances if d <= VERY_STRONG_IMAGE_DISTANCE)
    return best_distance, candidate, strong, very


def match_identity(
    old_hashes: list[int],
    new_hashes: list[int],
    *,
    old_phones: set[str] | None = None,
    new_phone: str | None = None,
    old_source_profile_ids: set[str] | None = None,
    new_source_profile_id: str | None = None,
) -> MatchResult:
    old_phones = old_phones or set()
    old_source_profile_ids = old_source_profile_ids or set()
    source_match = bool(new_source_profile_id and new_source_profile_id in old_source_profile_ids)
    phone_match = bool(new_phone and new_phone in old_phones)
    best, candidate, strong, very = compare_hash_sets(old_hashes, new_hashes)
    LOGGER.debug("Identity comparison old_images=%s new_images=%s best=%s candidate=%s strong=%s very=%s phone=%s source_profile=%s", len(old_hashes), len(new_hashes), best, candidate, strong, very, phone_match, source_match)

    if source_match:
        return MatchResult(
            "MATCH", "Gleiche websiteeigene Ladies-Profil-ID.", best, candidate, strong, very,
            phone_match=phone_match, source_profile_match=True,
        )
    if phone_match:
        return MatchResult(
            "MATCH", "Gleiche normalisierte Telefonnummer.", best, candidate, strong, very,
            phone_match=True,
        )
    if strong >= 2:
        return MatchResult(
            "MATCH", f"{strong} starke Bildtreffer (dHash ≤ {STRONG_IMAGE_DISTANCE}).",
            best, candidate, strong, very,
        )
    if very >= 1:
        return MatchResult(
            "STRONG_CANDIDATE", f"Ein sehr starker Bildtreffer (dHash ≤ {VERY_STRONG_IMAGE_DISTANCE}).",
            best, candidate, strong, very,
        )
    if candidate >= 1:
        return MatchResult(
            "CANDIDATE", f"Bildähnlichkeit im Kandidatenbereich (dHash ≤ {CANDIDATE_IMAGE_DISTANCE}).",
            best, candidate, strong, very,
        )
    return MatchResult("NO_MATCH", "Keine ausreichende Identity-Evidence.", best, candidate, strong, very)
