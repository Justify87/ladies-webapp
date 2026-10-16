from __future__ import annotations

from collections.abc import Iterable, Mapping

ScopeKey = tuple[int, int | None]
ScopeValue = tuple[int, str]


def _average_cents(values: Iterable[int]) -> int | None:
    amounts = [int(value) for value in values if int(value) >= 0]
    if not amounts:
        return None
    return (sum(amounts) + len(amounts) // 2) // len(amounts)


def resolve_map_price_60_cents(
    listing_people: Mapping[int, int],
    current_scopes: Mapping[ScopeKey, ScopeValue],
    exact_amounts_by_price_list: Mapping[int, Iterable[int]],
) -> dict[int, int]:
    """Project one unambiguous 60-minute EUR price per active listing.

    Listing-scoped pricing owns that listing whenever such a scope exists. A
    person-wide scope is only a fallback when no listing-scoped price revision
    exists at all. This prevents older or broader prices from leaking back into
    a location after a newer location-specific revision removed or changed the
    60-minute offer.
    """

    resolved: dict[int, int] = {}
    for raw_listing_id, raw_person_id in listing_people.items():
        listing_id = int(raw_listing_id)
        person_id = int(raw_person_id)
        scope = current_scopes.get((person_id, listing_id))
        if scope is None:
            scope = current_scopes.get((person_id, None))
        if scope is None:
            continue

        price_list_id, currency = scope
        if str(currency or "").upper() != "EUR":
            continue
        amount = _average_cents(exact_amounts_by_price_list.get(int(price_list_id), ()))
        if amount is not None:
            resolved[listing_id] = amount
    return resolved
