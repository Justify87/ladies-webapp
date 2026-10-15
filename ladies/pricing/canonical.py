from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select

from ladies_v9.models import Person, PriceList

from .service import PriceIntelligenceService

CANONICAL_PRICE_SOURCE = "WHATSAPP_EXTRACTED"


class CanonicalPriceIntelligenceService(PriceIntelligenceService):
    """Pricing service whose canonical revisions come from accepted chat extraction."""

    def _canonical_price_list(self, db, person_id: int) -> PriceList | None:
        return db.scalar(
            select(PriceList)
            .where(
                PriceList.person_id == int(person_id),
                PriceList.source == CANONICAL_PRICE_SOURCE,
            )
            .order_by(desc(PriceList.observed_at), desc(PriceList.price_list_id))
            .limit(1)
        )

    def _canonical_price_list_for_listing(
        self,
        db,
        person_id: int,
        listing_id: int | None,
    ) -> PriceList | None:
        query = select(PriceList).where(
            PriceList.person_id == int(person_id),
            PriceList.source == CANONICAL_PRICE_SOURCE,
        )
        query = query.where(
            PriceList.listing_id.is_(None) if listing_id is None else PriceList.listing_id == int(listing_id)
        )
        return db.scalar(
            query.order_by(desc(PriceList.observed_at), desc(PriceList.price_list_id)).limit(1)
        )

    def _current_proposal(self, db, person_id: int) -> tuple[int | None, dict[str, Any] | None]:
        row = self._canonical_price_list(db, person_id)
        return (row.price_list_id, self._proposal_from_price_list(db, row)) if row else (None, None)

    def _current_proposal_for_listing(
        self,
        db,
        person_id: int,
        listing_id: int | None,
    ) -> tuple[int | None, dict[str, Any] | None]:
        row = self._canonical_price_list_for_listing(db, person_id, listing_id)
        return (row.price_list_id, self._proposal_from_price_list(db, row)) if row else (None, None)

    def current(self, person_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            if db.get(Person, int(person_id)) is None:
                raise KeyError(person_id)
            row = self._canonical_price_list(db, int(person_id))
            if row is None:
                return {
                    "person_id": int(person_id),
                    "price_list_id": None,
                    "listing_id": None,
                    "location": None,
                    "observed_at": None,
                    "source": CANONICAL_PRICE_SOURCE,
                    "proposal": None,
                }
            return {
                "person_id": int(person_id),
                "price_list_id": row.price_list_id,
                "listing_id": row.listing_id,
                "location": self._price_list_location_payload(db, row),
                "observed_at": row.observed_at.isoformat() if row.observed_at else None,
                "source": row.source,
                "proposal": self._proposal_from_price_list(db, row),
            }

    def history(self, person_id: int, limit: int = 30) -> list[dict[str, Any]]:
        with self.database.session() as db:
            if db.get(Person, int(person_id)) is None:
                raise KeyError(person_id)
            rows = db.scalars(
                select(PriceList)
                .where(
                    PriceList.person_id == int(person_id),
                    PriceList.source == CANONICAL_PRICE_SOURCE,
                )
                .order_by(desc(PriceList.observed_at), desc(PriceList.price_list_id))
                .limit(max(1, min(100, int(limit))))
            ).all()
            return [self._price_list_payload(db, row) for row in rows]