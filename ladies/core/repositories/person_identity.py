from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ladies_v9.identity import MatchResult, hash_from_hex, match_identity
from ladies_v9.models import Listing, Person, PersonIdentifier, SourceProfile


def _match_rank(item: tuple[int, MatchResult]) -> tuple[int, int, int, int, int, int]:
    match = item[1]
    decision_rank = {
        "MATCH": 3,
        "STRONG_CANDIDATE": 2,
        "CANDIDATE": 1,
        "NO_MATCH": 0,
    }.get(match.decision, 0)
    return (
        decision_rank,
        int(match.source_profile_match),
        int(match.phone_match),
        match.strong_image_matches,
        match.very_strong_image_matches,
        -(match.best_distance if match.best_distance is not None else 999),
    )


def resolve_identity(
    db: Session,
    *,
    listing: Listing,
    source_profile_external_id: str | None,
    normalized_phone: str | None,
    image_hashes: list[int],
) -> tuple[int, MatchResult, int | None]:
    """Resolve the canonical person for a listing from persisted identity evidence."""
    if listing.match_decision == "MANUAL_SPLIT":
        return (
            listing.person_id,
            MatchResult(
                "MANUAL_SPLIT",
                "Manuelle Trennung: automatisches Re-Merge gesperrt.",
            ),
            None,
        )

    evaluated: list[tuple[int, MatchResult]] = []
    candidates = db.scalars(
        select(Person).where(Person.person_id != listing.person_id)
    ).all()
    for person in candidates:
        source_ids = set(db.scalars(
            select(SourceProfile.external_profile_id).where(
                SourceProfile.person_id == person.person_id,
                SourceProfile.external_profile_id.is_not(None),
            )
        ).all())
        phones = set(db.scalars(
            select(PersonIdentifier.normalized_value).where(
                PersonIdentifier.person_id == person.person_id,
                PersonIdentifier.kind == "PHONE",
            )
        ).all())
        hash_values = set(db.scalars(
            select(PersonIdentifier.normalized_value).where(
                PersonIdentifier.person_id == person.person_id,
                PersonIdentifier.kind == "IMAGE_DHASH",
            )
        ).all())
        old_hashes: list[int] = []
        for value in hash_values:
            try:
                old_hashes.append(hash_from_hex(value))
            except ValueError:
                pass

        match = match_identity(
            old_hashes,
            image_hashes,
            old_phones=phones,
            new_phone=normalized_phone,
            old_source_profile_ids=source_ids,
            new_source_profile_id=source_profile_external_id,
        )
        if match.decision != "NO_MATCH":
            evaluated.append((person.person_id, match))

    matches = sorted(
        [item for item in evaluated if item[1].decision == "MATCH"],
        key=_match_rank,
        reverse=True,
    )
    if matches and (len(matches) == 1 or _match_rank(matches[0]) > _match_rank(matches[1])):
        return matches[0][0], matches[0][1], None

    weak = sorted(
        [
            item for item in evaluated
            if item[1].decision in {"STRONG_CANDIDATE", "CANDIDATE"}
        ],
        key=_match_rank,
        reverse=True,
    )
    if weak:
        return listing.person_id, weak[0][1], weak[0][0]

    return (
        listing.person_id,
        MatchResult("NO_MATCH", "Keine ausreichende Identity-Evidence."),
        None,
    )
