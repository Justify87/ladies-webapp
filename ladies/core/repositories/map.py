from __future__ import annotations

from sqlalchemy import desc, select

from ladies_v9.database import Database
from ladies_v9.models import Listing, ListingSnapshot, SearchResult, SearchRun


class MapRepository:
    """Read-only persistence boundary for map data tied to one search run."""

    def __init__(self, database: Database):
        self.database = database

    def search_context(self, search_run_id: int) -> dict:
        with self.database.session() as db:
            run = db.get(SearchRun, int(search_run_id))
            if run is None:
                raise KeyError(search_run_id)

            rows = db.execute(
                select(SearchResult, Listing)
                .join(Listing, Listing.listing_id == SearchResult.listing_id)
                .where(SearchResult.search_run_id == run.search_run_id)
                .order_by(SearchResult.position)
            ).all()
            listing_ids = [int(listing.listing_id) for _result, listing in rows]

            latest_by_listing: dict[int, ListingSnapshot] = {}
            if listing_ids:
                snapshots = db.scalars(
                    select(ListingSnapshot)
                    .where(ListingSnapshot.listing_id.in_(listing_ids))
                    .order_by(
                        ListingSnapshot.listing_id,
                        desc(ListingSnapshot.captured_at),
                        desc(ListingSnapshot.snapshot_id),
                    )
                ).all()
                for snapshot in snapshots:
                    latest_by_listing.setdefault(int(snapshot.listing_id), snapshot)

            items = []
            seen_people: set[int] = set()
            for result, listing in rows:
                person_id = int(listing.person_id)
                if person_id in seen_people:
                    continue
                seen_people.add(person_id)
                snapshot = latest_by_listing.get(int(listing.listing_id))
                items.append({
                    "person_id": person_id,
                    "listing_id": int(listing.listing_id),
                    "advert_id": str(listing.external_listing_id),
                    "position": int(result.position),
                    "name": (snapshot.name if snapshot else None) or result.name or "Unbekannt",
                    "city": (snapshot.city if snapshot else None) or result.city or run.city or "",
                    "postal_code": snapshot.postal_code if snapshot else None,
                    "hidden_location": snapshot.hidden_location if snapshot else None,
                    "thumbnail_url": result.thumbnail_url,
                })

            return {
                "search_run_id": int(run.search_run_id),
                "city": run.city or "",
                "city_slug": run.city_slug or "",
                "radius_km": int(run.radius_km or 0),
                "items": items,
            }
