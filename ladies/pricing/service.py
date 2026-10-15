from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import desc, func, or_, select

from ladies_v9.models import Listing, PriceItem, PriceList, utcnow
from ladies_v9.messaging.models import ChatMessage, ChatThread
from ladies_v9.search_catalog import FILTER_VALUE_MAP

from .context import build_context
from .detector import PriceMessageDetector
from .extraction_core import PriceExtractionCore, dumps, loads
from .models import (
    PriceCondition,
    PriceEvidence,
    PriceExtraction,
    PriceExtractionMessage,
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
            current_id, current = self._current_proposal(db, person_id)

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

    def _prepare_review(self, db, extraction: PriceExtraction, data: dict[str, Any]):
        original = validate_proposal(loads(extraction.original_proposal_json, {}))
        reviewed_raw = data.get("proposal") if isinstance(data, dict) else None
        reviewed = validate_proposal(reviewed_raw if isinstance(reviewed_raw, dict) else original)
        user_edited = dumps(reviewed) != dumps(original)
        if reviewed["update_mode"] == "UNKNOWN":
            reviewed["update_mode"] = "PARTIAL" if extraction.base_price_list_id else "FULL"
        if not user_edited:
            self._validate_grounding(db, extraction, reviewed)
        current = self._proposal_from_price_list(db, db.get(PriceList, extraction.base_price_list_id)) if extraction.base_price_list_id else None
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
        raw = data.get("listing_id") if isinstance(data, dict) else None
        listing_id = int(raw) if raw not in (None, "") else self._suggested_listing_id(db, extraction)
        if listing_id is None:
            return None
        listing = db.get(Listing, listing_id)
        if listing is None or extraction.person_id is None or listing.person_id != int(extraction.person_id):
            raise ValueError("Gewählter Ort gehört nicht zu dieser Person")
        return listing_id

    def accept(self, extraction_id: int, data: dict[str, Any]) -> dict[str, Any]:
        with self.database.session() as db:
            extraction = db.get(PriceExtraction, int(extraction_id))
            if extraction is None:
                raise KeyError(extraction_id)
            if extraction.status != "PENDING":
                raise ValueError("Vorschlag wurde bereits bearbeitet")
            if extraction.person_id is None:
                raise ValueError("Preisvorschlag ist keiner Person mehr zugeordnet")

            latest_id, _ = self._current_proposal(db, int(extraction.person_id))
            if latest_id != extraction.base_price_list_id:
                extraction.status = "STALE"
                extraction.error = "Neuere Preisrevision vorhanden; Vorschlag muss neu erkannt werden"
                extraction.reviewed_at = utcnow()
                return {
                    "price_extraction_id": extraction.price_extraction_id,
                    "status": "STALE",
                    "latest_price_list_id": latest_id,
                    "error": extraction.error,
                }

            reviewed, effective, current, user_edited = self._prepare_review(db, extraction, data)
            listing_id = self._review_listing_id(db, extraction, data)
            fingerprint = self._proposal_fingerprint(effective)
            legacy_items = self._legacy_items(db, extraction.base_price_list_id)

            if extraction.base_price_list_id:
                base_list = db.get(PriceList, extraction.base_price_list_id)
                current_meta = db.get(PriceListRevisionMeta, extraction.base_price_list_id)
                current_fp = current_meta.fingerprint if current_meta else self._proposal_fingerprint(current or empty_proposal())
                full_would_remove_legacy = reviewed["update_mode"] == "FULL" and bool(legacy_items)
                location_changed = (base_list.listing_id if base_list else None) != listing_id
                if current_fp == fingerprint and not full_would_remove_legacy and not location_changed:
                    extraction.status = "ACCEPTED_NO_CHANGE"
                    extraction.reviewed_proposal_json = dumps(reviewed)
                    extraction.reviewed_at = utcnow()
                    extraction.accepted_price_list_id = extraction.base_price_list_id
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

            self._persist_offers(db, price_list, extraction, effective, user_edited=user_edited)
            if reviewed["update_mode"] == "PARTIAL" and extraction.base_price_list_id:
                self._clone_legacy_items(db, extraction.base_price_list_id, price_list.price_list_id)
            self._persist_rules_and_conditions(db, price_list, extraction, effective, user_edited=user_edited)

            db.add(PriceListRevisionMeta(
                price_list_id=price_list.price_list_id,
                supersedes_price_list_id=extraction.base_price_list_id,
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
