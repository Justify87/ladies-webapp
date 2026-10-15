from __future__ import annotations

from sqlalchemy import select

from ladies_v9.database import Database
from ladies_v9.models import Listing, ListingSnapshot


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
                })
            return items
