from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import desc, func, or_, select

from ladies_v9.models import Listing, ListingSnapshot, PriceItem, PriceList, utcnow
from ladies_v9.messaging.models import ChatMessage, ChatThread, ContactEndpoint
from ladies_v9.search_catalog import FILTER_VALUE_MAP

from .context import build_context
from .detector import PriceMessageDetector
from .extraction_core import PriceExtractionCore, dumps, loads
from .grounding import validate_proposal_grounding
from .models import (
    PriceCondition,
    PriceEvidence,
    PriceExtraction,
    PriceExtractionLocation,
    PriceExtractionMessage,
    PriceListLocationSnapshot,
    PriceListRevisionMeta,
    PriceOfferMeta,
    PriceServiceRule,
)
from .proposal_ops import merge_partial
from .schema import empty_proposal, validate_proposal

HANDLED_STATUSES = {"ACCEPTED", "ACCEPTED_NO_CHANGE", "REJECTED"}
TIMELINE_STATUSES = {"PENDING", "ACCEPTED", "ACCEPTED_NO_CHANGE", "STALE"}
SERVICE_CATALOG = tuple(
    {"label": str(label), "key": str(key)}
    for label, key in FILTER_VALUE_MAP.get("service", {}).items()
)
SERVICE_TERMS = tuple(
    dict.fromkeys(
        [row["label"] for row in SERVICE_CATALOG]
        + [row["key"] for row in SERVICE_CATALOG]
    )
)


class PriceIntelligenceService(PriceExtractionCore):
    """Two-stage price intelligence: deterministic detection, FunctionGemma extraction."""

    def __init__(self, database, data_dir):
        super().__init__(database, data_dir)
        self.detector = PriceMessageDetector(SERVICE_TERMS)

    def _handled_through_message_id(self, db, thread_id: int) -> int:
        value = db.scalar(
            select(func.max(PriceExtractionMessage.message_id))
            .join(PriceExtraction, PriceExtraction.price_extraction_id == PriceExtractionMessage.price_extraction_id)
            .where(
                PriceExtraction.thread_id == int(thread_id),
                PriceExtraction.status.in_(HANDLED_STATUSES),
            )
        )
        return int(value or 0)

    def signal(self, thread_id: int, *, allow_ocr: bool = True) -> dict[str, Any]:
        settings = self.settings()
        if not settings["price_intelligence_enabled"]:
            return {"thread_id": thread_id, "enabled": False, "strong": False, "reasons": []}
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            if thread.person_id is None:
                return {
                    "thread_id": thread_id,
                    "enabled": True,
                    "strong": False,
                    "classification": "OTHER",
                    "reasons": ["unassigned"],
                }

            handled_through = self._handled_through_message_id(db, thread.thread_id)
            rows = db.scalars(
                select(ChatMessage)
                .where(ChatMessage.thread_id == thread.thread_id, ChatMessage.direction.in_(["IN", "OUT"]))
                .order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id))
                .limit(12)
            ).all()
            rows.reverse()
            seed = None
            reasons: list[str] = []
            ocr_status = None
            previous_outgoing = None
            previous_outgoing_at = None
            contains_image = False
            for message in rows:
                derived = self._ensure_derived(db, message, run_ocr=allow_ocr and message.message_type == "IMAGE")
                text = self._effective_text(message, derived)
                if message.direction == "OUT":
                    if message.message_id > handled_through:
                        previous_outgoing = text
                        previous_outgoing_at = message.timestamp
                    continue
                if message.message_id <= handled_through:
                    continue
                contains_image = contains_image or message.message_type == "IMAGE"
                question = previous_outgoing
                if question and previous_outgoing_at is not None:
                    try:
                        if message.timestamp - previous_outgoing_at > timedelta(minutes=30):
                            question = None
                    except TypeError:
                        pass
                result = self.detector.detect(
                    text,
                    previous_outgoing=question,
                    has_image=message.message_type == "IMAGE",
                )
                if result.is_price_message:
                    seed = message.message_id
                    reasons = list(result.reasons)
                    ocr_status = derived.ocr_status

            strong = seed is not None
            return {
                "thread_id": thread.thread_id,
                "person_id": thread.person_id,
                "enabled": True,
                "magic_button_enabled": settings["price_magic_button_enabled"],
                "strong": strong,
                "classification": "PRICE_RELATED" if strong else "OTHER",
                "reasons": reasons,
                "seed_message_id": seed,
                "contains_image": contains_image,
                "ocr_status": ocr_status,
                "ocr_available": self.ocr.available(),
                "detector": {"name": self.detector.name, "version": self.detector.version},
                "handled_through_message_id": handled_through,
            }

    def timeline(self, thread_id: int, *, limit: int = 30) -> dict[str, Any]:
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            rows = db.scalars(
                select(PriceExtraction)
                .where(
                    PriceExtraction.thread_id == thread.thread_id,
                    PriceExtraction.status.in_(TIMELINE_STATUSES),
                )
                .order_by(desc(PriceExtraction.created_at), desc(PriceExtraction.price_extraction_id))
                .limit(max(1, min(100, int(limit))))
            ).all()
            items = []
            for extraction in reversed(rows):
                current = (
                    self._proposal_from_price_list(db, db.get(PriceList, extraction.base_price_list_id))
                    if extraction.base_price_list_id
                    else None
                )
                payload = self._extraction_payload(db, extraction, current=current)
                messages = payload.get("messages") or []
                primary = next((x for x in messages if x.get("role") == "PRIMARY"), None)
                fallback = next((x for x in reversed(messages) if x.get("role") != "QUESTION"), None)
                anchor = primary or fallback or (messages[-1] if messages else None)
                payload["anchor_message_id"] = int(anchor["message_id"]) if anchor else None
                items.append(payload)
            return {"thread_id": thread.thread_id, "items": items}

    def _current_proposal_for_listing(
        self,
        db,
        person_id: int,
        listing_id: int | None,
    ) -> tuple[int | None, dict[str, Any] | None]:
        query = select(PriceList).where(PriceList.person_id == int(person_id))
        query = query.where(
            PriceList.listing_id.is_(None) if listing_id is None else PriceList.listing_id == int(listing_id)
        )
        row = db.scalar(query.order_by(desc(PriceList.observed_at), desc(PriceList.price_list_id)).limit(1))
        return (row.price_list_id, self._proposal_from_price_list(db, row)) if row else (None, None)

    def _listing_id_from_message(self, db, person_id: int, message_id: int) -> int | None:
        message = db.get(ChatMessage, int(message_id))
        endpoint = db.get(ContactEndpoint, message.endpoint_id) if message and message.endpoint_id else None
        listing_id = int(endpoint.source_listing_id) if endpoint and endpoint.source_listing_id else None
        if listing_id is None:
            return None
        listing = db.get(Listing, listing_id)
        if listing is None or listing.person_id != int(person_id):
            return None
        return listing_id

    def _suggested_listing_id_for_messages(self, db, person_id: int, message_ids: list[int]) -> int | None:
        for message_id in message_ids:
            listing_id = self._listing_id_from_message(db, person_id, message_id)
            if listing_id is not None:
                return listing_id
        return None

    def _suggested_listing_id(self, db, extraction: PriceExtraction) -> int | None:
        scope = db.get(PriceExtractionLocation, extraction.price_extraction_id)
        if scope is not None:
            return int(scope.suggested_listing_id) if scope.suggested_listing_id else None
        if extraction.base_price_list_id:
            base = db.get(PriceList, extraction.base_price_list_id)
            if base is not None:
                return int(base.listing_id) if base.listing_id else None
        anchors = db.scalars(
            select(PriceExtractionMessage)
            .where(PriceExtractionMessage.price_extraction_id == extraction.price_extraction_id)
            .order_by(PriceExtractionMessage.position)
        ).all()
        ordered = [x for x in anchors if x.role == "PRIMARY"] + [x for x in anchors if x.role != "PRIMARY"]
        if extraction.person_id is None:
            return None
        return self._suggested_listing_id_for_messages(
            db,
            int(extraction.person_id),
            [int(anchor.message_id) for anchor in ordered],
        )

    def create_proposal(
        self,
        thread_id: int,
        *,
        trigger_type: str = "MANUAL",
        seed_message_id: int | None = None,
    ) -> dict[str, Any]:
        if not self.settings()["price_intelligence_enabled"]:
            raise ValueError("Preiserkennung ist deaktiviert")
        if seed_message_id is None:
            signal = self.signal(thread_id)
            seed_message_id = signal.get("seed_message_id")
            if not seed_message_id:
                raise ValueError("Keine preisrelevante Nachricht erkannt")

        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            if thread.person_id is None:
                raise ValueError("Chat ist keiner Person zugeordnet")
            context = build_context(
                thread_id=thread.thread_id,
                person_id=int(thread.person_id),
                rows=self._context_rows(db, thread),
                seed_message_id=seed_message_id,
            )
            person_id = int(thread.person_id)
            location_message_ids = [int(seed_message_id)] + [
                int(message.message_id)
                for message in reversed(context.messages)
                if int(message.message_id) != int(seed_message_id)
            ]
            suggested_listing_id = self._suggested_listing_id_for_messages(
                db, person_id, location_message_ids
            )
            current_id, current = self._current_proposal_for_listing(
                db, person_id, suggested_listing_id
            )

        proposal = self.semantic.extract_price_proposal(
            context.as_text(),
            service_catalog=list(SERVICE_CATALOG),
            has_existing_price_list=current_id is not None,
        )
        if not (proposal["offers"] or proposal["service_rules"] or proposal["conditions"]):
            raise ValueError("FunctionGemma hat in diesem Chatabschnitt keine belastbaren Preisangaben extrahiert")

        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None or thread.person_id != person_id:
                raise ValueError("Chat-Zuordnung hat sich während der Preiserkennung geändert")
            extraction = PriceExtraction(
                person_id=person_id,
                thread_id=thread.thread_id,
                status="PENDING",
                trigger_type=str(trigger_type or "MANUAL").upper()[:24],
                schema_version=proposal["schema_version"],
                extractor_name=self.semantic.name,
                extractor_version=self.semantic.version,
                original_proposal_json=dumps(proposal),
                context_fingerprint=context.fingerprint,
                base_price_list_id=current_id,
            )
            db.add(extraction)
            db.flush()
            db.add(PriceExtractionLocation(
                price_extraction_id=extraction.price_extraction_id,
                suggested_listing_id=suggested_listing_id,
            ))
            for position, message in enumerate(context.messages):
                role = (
                    "PRIMARY"
                    if message.message_id == seed_message_id
                    else "IMAGE_OCR"
                    if message.message_type == "IMAGE" and message.ocr_text
                    else "QUESTION"
                    if message.direction == "OUT"
                    else "CONTEXT"
                )
                db.add(
                    PriceExtractionMessage(
                        price_extraction_id=extraction.price_extraction_id,
                        message_id=message.message_id,
                        role=role,
                        position=position,
                    )
                )
            db.flush()
            self._validate_grounding(db, extraction, proposal)
            return self._extraction_payload(db, extraction, current=current)

    def _legacy_items(self, db, price_list_id: int | None) -> list[PriceItem]:
        if not price_list_id:
            return []
        return db.scalars(
            select(PriceItem)
            .where(
                PriceItem.price_list_id == int(price_list_id),
                or_(PriceItem.item_type != "BASE", PriceItem.service.is_not(None)),
            )
            .order_by(PriceItem.price_item_id)
        ).all()

    def _proposal_from_price_list(self, db, row: PriceList | None) -> dict[str, Any] | None:
        proposal = super()._proposal_from_price_list(db, row)
        if row is None or proposal is None:
            return proposal
        items = db.scalars(
            select(PriceItem).where(PriceItem.price_list_id == row.price_list_id).order_by(PriceItem.price_item_id)
        ).all()
        proposal["offers"] = [
            offer for item, offer in zip(items, proposal["offers"])
            if item.item_type == "BASE" and not item.service
        ]
        return validate_proposal(proposal)

    def _clone_legacy_items(self, db, source_price_list_id: int, target_price_list_id: int) -> None:
        for old in self._legacy_items(db, source_price_list_id):
            new = PriceItem(
                price_list_id=target_price_list_id,
                item_type=old.item_type,
                label=old.label,
                duration_minutes=old.duration_minutes,
                service=old.service,
                amount_cents=old.amount_cents,
                note=old.note,
            )
            db.add(new)
            db.flush()
            meta = db.get(PriceOfferMeta, old.price_item_id)
            if meta is not None:
                db.add(PriceOfferMeta(
                    price_item_id=new.price_item_id,
                    duration_max_minutes=meta.duration_max_minutes,
                    amount_type=meta.amount_type,
                    amount_max_cents=meta.amount_max_cents,
                ))
            evidence = db.scalars(select(PriceEvidence).where(PriceEvidence.price_item_id == old.price_item_id)).all()
            for old_ev in evidence:
                db.add(PriceEvidence(
                    price_list_id=target_price_list_id,
                    price_item_id=new.price_item_id,
                    message_id=old_ev.message_id,
                    extraction_id=old_ev.extraction_id,
                    source_type=old_ev.source_type,
                    excerpt=old_ev.excerpt,
                ))

    def _validate_grounding(self, db, extraction: PriceExtraction, proposal: dict[str, Any]) -> None:
        validate_proposal_grounding(db, extraction, proposal, self._effective_text)

    def _prepare_review(
        self,
        db,
        extraction: PriceExtraction,
        data: dict[str, Any],
        *,
        base_price_list_id: int | None,
        current: dict[str, Any] | None,
    ):
        original = validate_proposal(loads(extraction.original_proposal_json, {}))
        reviewed_raw = data.get("proposal") if isinstance(data, dict) else None
        reviewed = validate_proposal(reviewed_raw if isinstance(reviewed_raw, dict) else original)
        user_edited = dumps(reviewed) != dumps(original)
        if reviewed["update_mode"] == "UNKNOWN":
            reviewed["update_mode"] = "PARTIAL" if base_price_list_id else "FULL"
        if not user_edited:
            self._validate_grounding(db, extraction, reviewed)
        effective = merge_partial(current, reviewed) if reviewed["update_mode"] == "PARTIAL" else validate_proposal(reviewed)
        effective["update_mode"] = "FULL"
        return reviewed, effective, current, user_edited

    def _persist_offers(self, db, price_list: PriceList, extraction: PriceExtraction, effective: dict[str, Any], *, user_edited: bool) -> None:
        for offer in effective["offers"]:
            if offer.get("amount_cents") is None:
                continue
            item = PriceItem(
                price_list_id=price_list.price_list_id,
                item_type="BASE",
                label=offer.get("label") or (f"{offer['duration_minutes']} Minuten" if offer.get("duration_minutes") else "Preis"),
                duration_minutes=offer.get("duration_minutes"),
                amount_cents=int(offer["amount_cents"]),
                note=None,
            )
            db.add(item)
            db.flush()
            db.add(PriceOfferMeta(
                price_item_id=item.price_item_id,
                duration_max_minutes=offer.get("duration_max_minutes"),
                amount_type=offer.get("amount_type") or "EXACT",
                amount_max_cents=offer.get("amount_max_cents"),
            ))
            self._save_evidence(
                db,
                price_list.price_list_id,
                extraction.price_extraction_id,
                offer.get("evidence"),
                price_item_id=item.price_item_id,
                user_edited=user_edited,
            )

    def _persist_rules_and_conditions(self, db, price_list: PriceList, extraction: PriceExtraction, effective: dict[str, Any], *, user_edited: bool) -> None:
        for position, rule in enumerate(effective["service_rules"]):
            row = PriceServiceRule(
                price_list_id=price_list.price_list_id,
                service_key=rule.get("service_key"),
                raw_label=rule["raw_label"],
                status=rule["status"],
                amount_cents=rule.get("amount_cents"),
                note=rule.get("note"),
                position=position,
            )
            db.add(row)
            db.flush()
            self._save_evidence(
                db,
                price_list.price_list_id,
                extraction.price_extraction_id,
                rule.get("evidence"),
                price_service_rule_id=row.price_service_rule_id,
                user_edited=user_edited,
            )

        for position, condition in enumerate(effective["conditions"]):
            row = PriceCondition(
                price_list_id=price_list.price_list_id,
                condition_type=condition["condition_type"],
                label=condition["label"],
                amount_cents=condition.get("amount_cents"),
                note=condition.get("note"),
                position=position,
            )
            db.add(row)
            db.flush()
            self._save_evidence(
                db,
                price_list.price_list_id,
                extraction.price_extraction_id,
                condition.get("evidence"),
                price_condition_id=row.price_condition_id,
                user_edited=user_edited,
            )

    def _review_listing_id(self, db, extraction: PriceExtraction, data: dict[str, Any]) -> int | None:
        if isinstance(data, dict) and "listing_id" in data:
            raw = data.get("listing_id")
            listing_id = int(raw) if raw not in (None, "") else None
        else:
            listing_id = self._suggested_listing_id(db, extraction)
        if listing_id is None:
            return None
        listing = db.get(Listing, listing_id)
        if listing is None or extraction.person_id is None or listing.person_id != int(extraction.person_id):
            raise ValueError("Gewählter Ort gehört nicht zu dieser Person")
        return listing_id

    @staticmethod
    def _snapshot_location_payload(listing: Listing, snapshot: ListingSnapshot | None) -> dict[str, Any]:
        address = snapshot.hidden_location if snapshot else None
        city = snapshot.city if snapshot else None
        postal_code = snapshot.postal_code if snapshot else None
        locality = " ".join(part for part in (postal_code, city) if part)
        parts: list[str] = []
        for value in (address, locality, listing.external_listing_id):
            text = str(value or "").strip()
            if text and text not in parts:
                parts.append(text)
        return {
            "listing_id": listing.listing_id,
            "listing_snapshot_id": snapshot.snapshot_id if snapshot else None,
            "label": " · ".join(parts) or f"Inserat {listing.listing_id}",
            "city": city,
            "postal_code": postal_code,
            "address": address,
            "external_listing_id": listing.external_listing_id,
        }

    def _persist_price_list_location(self, db, price_list: PriceList) -> None:
        if price_list.listing_id is None:
            db.add(PriceListLocationSnapshot(price_list_id=price_list.price_list_id))
            return
        listing = db.get(Listing, int(price_list.listing_id))
        if listing is None:
            db.add(PriceListLocationSnapshot(
                price_list_id=price_list.price_list_id,
                listing_id=price_list.listing_id,
            ))
            return
        snapshot = db.get(ListingSnapshot, listing.current_snapshot_id) if listing.current_snapshot_id else None
        payload = self._snapshot_location_payload(listing, snapshot)
        db.add(PriceListLocationSnapshot(
            price_list_id=price_list.price_list_id,
            listing_id=listing.listing_id,
            listing_snapshot_id=payload["listing_snapshot_id"],
            label=payload["label"],
            city=payload["city"],
            postal_code=payload["postal_code"],
            address=payload["address"],
            external_listing_id=payload["external_listing_id"],
        ))

    def _price_list_location_payload(self, db, row: PriceList | None) -> dict[str, Any] | None:
        if row is None or row.listing_id is None:
            return None
        stored = db.get(PriceListLocationSnapshot, row.price_list_id)
        if stored is not None:
            return {
                "listing_id": stored.listing_id,
                "listing_snapshot_id": stored.listing_snapshot_id,
                "label": stored.label,
                "city": stored.city,
                "postal_code": stored.postal_code,
                "address": stored.address,
                "external_listing_id": stored.external_listing_id,
            }
        listing = db.get(Listing, int(row.listing_id))
        if listing is None:
            return None
        snapshot = db.scalar(
            select(ListingSnapshot)
            .where(
                ListingSnapshot.listing_id == listing.listing_id,
                ListingSnapshot.captured_at <= row.observed_at,
            )
            .order_by(desc(ListingSnapshot.captured_at), desc(ListingSnapshot.snapshot_id))
            .limit(1)
        )
        if snapshot is None and listing.current_snapshot_id:
            snapshot = db.get(ListingSnapshot, listing.current_snapshot_id)
        return self._snapshot_location_payload(listing, snapshot)

    def _price_list_payload(self, db, row: PriceList) -> dict[str, Any]:
        payload = super()._price_list_payload(db, row)
        payload["location"] = self._price_list_location_payload(db, row)
        return payload

    def _extraction_payload(self, db, row: PriceExtraction, *, current: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = super()._extraction_payload(db, row, current=current)
        if row.accepted_price_list_id:
            accepted = db.get(PriceList, row.accepted_price_list_id)
            payload["listing_id"] = accepted.listing_id if accepted else None
            payload["location"] = self._price_list_location_payload(db, accepted)
        else:
            listing_id = self._suggested_listing_id(db, row)
            payload["listing_id"] = listing_id
            payload["location"] = self._location_payload(db, listing_id)
        return payload

    def _set_selected_listing(self, db, extraction: PriceExtraction, listing_id: int | None) -> None:
        scope = db.get(PriceExtractionLocation, extraction.price_extraction_id)
        if scope is None:
            scope = PriceExtractionLocation(
                price_extraction_id=extraction.price_extraction_id,
                suggested_listing_id=self._suggested_listing_id(db, extraction),
            )
            db.add(scope)
        scope.selected_listing_id = listing_id
        scope.updated_at = utcnow()

    def accept(self, extraction_id: int, data: dict[str, Any]) -> dict[str, Any]:
        with self.database.session() as db:
            extraction = db.get(PriceExtraction, int(extraction_id))
            if extraction is None:
                raise KeyError(extraction_id)
            if extraction.status != "PENDING":
                raise ValueError("Vorschlag wurde bereits bearbeitet")
            if extraction.person_id is None:
                raise ValueError("Preisvorschlag ist keiner Person mehr zugeordnet")

            suggested_listing_id = self._suggested_listing_id(db, extraction)
            listing_id = self._review_listing_id(db, extraction, data)
            latest_id, current = self._current_proposal_for_listing(
                db, int(extraction.person_id), listing_id
            )
            if listing_id == suggested_listing_id and latest_id != extraction.base_price_list_id:
                extraction.status = "STALE"
                extraction.error = "Neuere Preisrevision für diesen Ort vorhanden; Vorschlag muss neu erkannt werden"
                extraction.reviewed_at = utcnow()
                return {
                    "price_extraction_id": extraction.price_extraction_id,
                    "status": "STALE",
                    "latest_price_list_id": latest_id,
                    "error": extraction.error,
                }

            base_price_list_id = extraction.base_price_list_id if listing_id == suggested_listing_id else latest_id
            current = (
                self._proposal_from_price_list(db, db.get(PriceList, base_price_list_id))
                if base_price_list_id
                else None
            )
            reviewed, effective, current, user_edited = self._prepare_review(
                db,
                extraction,
                data,
                base_price_list_id=base_price_list_id,
                current=current,
            )
            self._set_selected_listing(db, extraction, listing_id)
            fingerprint = self._proposal_fingerprint(effective)
            legacy_items = self._legacy_items(db, base_price_list_id)

            if base_price_list_id:
                current_meta = db.get(PriceListRevisionMeta, base_price_list_id)
                current_fp = current_meta.fingerprint if current_meta else self._proposal_fingerprint(current or empty_proposal())
                full_would_remove_legacy = reviewed["update_mode"] == "FULL" and bool(legacy_items)
                if current_fp == fingerprint and not full_would_remove_legacy:
                    extraction.status = "ACCEPTED_NO_CHANGE"
                    extraction.reviewed_proposal_json = dumps(reviewed)
                    extraction.reviewed_at = utcnow()
                    extraction.accepted_price_list_id = base_price_list_id
                    return self._extraction_payload(db, extraction, current=current)

            price_list = PriceList(
                person_id=int(extraction.person_id),
                listing_id=listing_id,
                observed_at=utcnow(),
                source="WHATSAPP_EXTRACTED",
                currency=effective["currency"],
                note=self._summary_note(effective),
            )
            db.add(price_list)
            db.flush()
            self._persist_price_list_location(db, price_list)

            self._persist_offers(db, price_list, extraction, effective, user_edited=user_edited)
            if reviewed["update_mode"] == "PARTIAL" and base_price_list_id:
                self._clone_legacy_items(db, base_price_list_id, price_list.price_list_id)
            self._persist_rules_and_conditions(db, price_list, extraction, effective, user_edited=user_edited)

            db.add(PriceListRevisionMeta(
                price_list_id=price_list.price_list_id,
                supersedes_price_list_id=base_price_list_id,
                source_extraction_id=extraction.price_extraction_id,
                revision_type=reviewed["update_mode"],
                completeness="MERGED_PARTIAL" if reviewed["update_mode"] == "PARTIAL" else "FULL",
                fingerprint=fingerprint,
            ))
            extraction.status = "ACCEPTED"
            extraction.reviewed_proposal_json = dumps(reviewed)
            extraction.reviewed_at = utcnow()
            extraction.accepted_price_list_id = price_list.price_list_id
            db.flush()
            return self._extraction_payload(db, extraction, current=current)