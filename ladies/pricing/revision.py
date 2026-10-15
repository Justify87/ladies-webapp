from __future__ import annotations

from copy import deepcopy
from hashlib import sha256

from sqlalchemy import desc, select

from ladies_v9.models import PriceList

from .extraction_core import dumps
from .models import PriceExtraction, PriceExtractionLocation, PriceExtractionMessage
from .schema import validate_proposal


def create_revision(service, extraction_id: int) -> dict:
    """Create an editable pending revision for the saved price list at its location."""
    with service.database.session() as db:
        source = db.get(PriceExtraction, int(extraction_id))
        if source is None:
            raise KeyError(extraction_id)
        if source.status not in {"ACCEPTED", "ACCEPTED_NO_CHANGE"}:
            raise ValueError("Nur gespeicherte Preisvorschläge können geändert werden")
        if source.person_id is None or source.thread_id is None:
            raise ValueError("Preisvorschlag ist keinem Chat mehr zugeordnet")
        if source.accepted_price_list_id is None:
            raise ValueError("Gespeicherte Preisliste wurde nicht gefunden")

        source_list = db.get(PriceList, int(source.accepted_price_list_id))
        if source_list is None:
            raise ValueError("Gespeicherte Preisliste wurde nicht gefunden")
        listing_id = int(source_list.listing_id) if source_list.listing_id else None
        latest_id, current = service._current_proposal_for_listing(
            db, int(source.person_id), listing_id
        )
        if latest_id is None or current is None:
            raise ValueError("Gespeicherte Preisliste wurde nicht gefunden")
        if source.accepted_price_list_id != latest_id:
            raise ValueError("Dieser Preisvorschlag wurde an diesem Ort bereits durch eine neuere Preisliste ersetzt")

        pending = db.scalar(
            select(PriceExtraction)
            .where(
                PriceExtraction.person_id == int(source.person_id),
                PriceExtraction.thread_id == int(source.thread_id),
                PriceExtraction.base_price_list_id == int(latest_id),
                PriceExtraction.status == "PENDING",
                PriceExtraction.trigger_type == "REVISION",
            )
            .order_by(desc(PriceExtraction.price_extraction_id))
            .limit(1)
        )
        if pending is not None:
            pending_scope = db.get(PriceExtractionLocation, pending.price_extraction_id)
            if pending_scope is not None and pending_scope.suggested_listing_id == listing_id:
                return service._extraction_payload(db, pending, current=current)

        proposal = validate_proposal(current)
        proposal["update_mode"] = "FULL"
        baseline = deepcopy(proposal)
        baseline["uncertainties"] = [*(baseline.get("uncertainties") or []), "USER_REVISION_BASELINE"]
        context = f"revision:{source.price_extraction_id}:{latest_id}:{listing_id or 'none'}"
        pending = PriceExtraction(
            person_id=int(source.person_id),
            thread_id=int(source.thread_id),
            status="PENDING",
            trigger_type="REVISION",
            schema_version=proposal["schema_version"],
            extractor_name="user_revision",
            extractor_version="2",
            original_proposal_json=dumps(baseline),
            reviewed_proposal_json=dumps(proposal),
            context_fingerprint=sha256(context.encode("utf-8")).hexdigest(),
            base_price_list_id=int(latest_id),
        )
        db.add(pending)
        db.flush()
        db.add(PriceExtractionLocation(
            price_extraction_id=pending.price_extraction_id,
            suggested_listing_id=listing_id,
        ))

        anchors = db.scalars(
            select(PriceExtractionMessage)
            .where(PriceExtractionMessage.price_extraction_id == source.price_extraction_id)
            .order_by(PriceExtractionMessage.position)
        ).all()
        for anchor in anchors:
            db.add(PriceExtractionMessage(
                price_extraction_id=pending.price_extraction_id,
                message_id=anchor.message_id,
                role=anchor.role,
                position=anchor.position,
            ))
        db.flush()
        return service._extraction_payload(db, pending, current=current)