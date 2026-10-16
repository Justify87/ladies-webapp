from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, or_, select

from ladies_v9.database import Database
from ladies_v9.models import Listing, ListingSnapshot, PriceItem, PriceList
from ladies_v9.pricing.map_projection import resolve_map_price_60_cents
from ladies_v9.pricing.models import PriceOfferMeta


class MapRepository:
    """Read-only persistence boundary for map data of the active result set."""

    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _price_60_by_listing(db, listing_people: dict[int, int]) -> dict[int, int]:
        if not listing_people:
            return {}

        person_ids = sorted(set(listing_people.values()))
        listing_ids = sorted(listing_people)
        scope_rank = func.row_number().over(
            partition_by=(PriceList.person_id, PriceList.listing_id),
            order_by=(PriceList.observed_at.desc(), PriceList.price_list_id.desc()),
        ).label("scope_rank")
        ranked_scopes = (
            select(
                PriceList.price_list_id.label("price_list_id"),
                PriceList.person_id.label("person_id"),
                PriceList.listing_id.label("listing_id"),
                PriceList.currency.label("currency"),
                scope_rank,
            )
            .where(
                PriceList.person_id.in_(person_ids),
                or_(PriceList.listing_id.in_(listing_ids), PriceList.listing_id.is_(None)),
            )
            .subquery()
        )
        scope_rows = db.execute(
            select(
                ranked_scopes.c.price_list_id,
                ranked_scopes.c.person_id,
                ranked_scopes.c.listing_id,
                ranked_scopes.c.currency,
            ).where(ranked_scopes.c.scope_rank == 1)
        ).all()
        current_scopes = {
            (
                int(row.person_id),
                int(row.listing_id) if row.listing_id is not None else None,
            ): (int(row.price_list_id), str(row.currency or ""))
            for row in scope_rows
        }
        if not current_scopes:
            return {}

        current_price_list_ids = sorted({price_list_id for price_list_id, _ in current_scopes.values()})
        item_rows = db.execute(
            select(PriceItem.price_list_id, PriceItem.amount_cents)
            .outerjoin(PriceOfferMeta, PriceOfferMeta.price_item_id == PriceItem.price_item_id)
            .where(
                PriceItem.price_list_id.in_(current_price_list_ids),
                PriceItem.item_type == "BASE",
                PriceItem.duration_minutes == 60,
                PriceItem.amount_cents.is_not(None),
                or_(PriceOfferMeta.price_item_id.is_(None), PriceOfferMeta.amount_type == "EXACT"),
            )
        ).all()
        exact_amounts_by_price_list: dict[int, list[int]] = defaultdict(list)
        for row in item_rows:
            exact_amounts_by_price_list[int(row.price_list_id)].append(int(row.amount_cents))

        return resolve_map_price_60_cents(
            listing_people,
            current_scopes,
            exact_amounts_by_price_list,
        )

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
            listing_people = {
                listing_id: int(listing.person_id)
                for listing_id, (listing, _) in by_listing.items()
            }
            price_60_by_listing = self._price_60_by_listing(db, listing_people)

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
