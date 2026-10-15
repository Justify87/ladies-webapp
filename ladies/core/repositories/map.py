from __future__ import annotations

from collections import defaultdict

from sqlalchemy import or_, select

from ladies_v9.database import Database
from ladies_v9.models import Listing, ListingSnapshot, PriceItem, PriceList


class MapRepository:
    """Read-only persistence boundary for map data of the active result set."""

    def __init__(self, database: Database):
        self.database = database

    def map_items(self, listing_ids: list[int]) -> list[dict]:
        ordered: list[int] = []
        seen_ids: set[int] = set()
        for raw in listing_ids:
            try:
                listing_id = int(raw)
            except (TypeError, ValueError):
                continue
            if listing_id > 0 and listing_id not in seen_ids:
                seen_ids.add(listing_id)
                ordered.append(listing_id)
        if not ordered:
            return []

        with self.database.session() as db:
            rows = db.execute(
                select(Listing, ListingSnapshot)
                .outerjoin(
                    ListingSnapshot,
                    ListingSnapshot.snapshot_id == Listing.current_snapshot_id,
                )
                .where(Listing.listing_id.in_(ordered))
            ).all()
            by_listing = {
                int(listing.listing_id): (listing, snapshot)
                for listing, snapshot in rows
            }

            listings_by_person: dict[int, list[int]] = defaultdict(list)
            for listing_id, (listing, _) in by_listing.items():
                listings_by_person[int(listing.person_id)].append(listing_id)

            price_60_by_listing: dict[int, int] = {}
            if listings_by_person:
                price_rows = db.execute(
                    select(PriceList, PriceItem)
                    .join(PriceItem, PriceItem.price_list_id == PriceList.price_list_id)
                    .where(
                        PriceList.person_id.in_(list(listings_by_person)),
                        or_(PriceList.listing_id.in_(ordered), PriceList.listing_id.is_(None)),
                        PriceList.currency == "EUR",
                        PriceItem.item_type == "BASE",
                        PriceItem.duration_minutes == 60,
                    )
                ).all()
                price_rows.sort(
                    key=lambda row: (
                        row[0].observed_at,
                        int(row[0].price_list_id),
                        -int(row[1].price_item_id),
                    ),
                    reverse=True,
                )
                for price_list, price_item in price_rows:
                    person_id = int(price_list.person_id)
                    if price_list.listing_id is None:
                        targets = listings_by_person.get(person_id, [])
                    else:
                        listing_id = int(price_list.listing_id)
                        targets = [listing_id] if listing_id in seen_ids else []
                    for listing_id in targets:
                        price_60_by_listing.setdefault(listing_id, int(price_item.amount_cents))

            items: list[dict] = []
            for listing_id in ordered:
                pair = by_listing.get(listing_id)
                if pair is None:
                    continue
                listing, snapshot = pair
                items.append({
                    "person_id": int(listing.person_id),
                    "listing_id": int(listing.listing_id),
                    "advert_id": str(listing.external_listing_id),
                    "city": snapshot.city if snapshot else None,
                    "postal_code": snapshot.postal_code if snapshot else None,
                    "hidden_location": snapshot.hidden_location if snapshot else None,
                    "price_60_cents": price_60_by_listing.get(listing_id),
                })
            return items
