from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select, update

from ladies_v9.database import Database
from ladies_v9.models import AppSetting, IdentityEvent, Person, PriceItem, PriceList, utcnow
from ladies_v9.messaging.models import ChatMessage, ChatThread

from .context import ContextMessage, build_context
from .model_runtime import FunctionGemmaRuntime
from .models import (
    ChatMessageDerived,
    PriceCondition,
    PriceEvidence,
    PriceExtraction,
    PriceExtractionMessage,
    PriceListRevisionMeta,
    PriceOfferMeta,
    PriceServiceRule,
)
from .normalize import extract_durations, extract_money
from .ocr import PaddleOcrV6TinyEngine
from .rules import extract_with_rules, merge_partial, proposal_diff
from .schema import empty_proposal, validate_proposal
from .signal import detect_price_signal

LOGGER = logging.getLogger("ladies_explorer.pricing")

SETTING_ENABLED = "price_intelligence_enabled"
SETTING_MAGIC = "price_magic_button_enabled"
SETTING_OCR = "price_ocr_enabled"
SETTING_MODEL = "price_semantic_model_enabled"
DEFAULT_SETTINGS = {
    SETTING_ENABLED: True,
    SETTING_MAGIC: True,
    SETTING_OCR: True,
    SETTING_MODEL: False,
}


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class PriceIntelligenceService:
    def __init__(self, db_path: str | Path, data_dir: str | Path):
        self.database = Database(db_path)
        self.database.create_schema()
        self.data_dir = Path(data_dir)
        self.ocr = PaddleOcrV6TinyEngine()
        self.semantic = FunctionGemmaRuntime(data_dir)
        self._ensure_settings()

    def _ensure_settings(self) -> None:
        with self.database.session() as db:
            for key, value in DEFAULT_SETTINGS.items():
                if db.get(AppSetting, key) is None:
                    db.add(AppSetting(key=key, value_json="true" if value else "false"))

    def settings(self) -> dict[str, bool]:
        with self.database.session() as db:
            out = dict(DEFAULT_SETTINGS)
            for key in DEFAULT_SETTINGS:
                row = db.get(AppSetting, key)
                if row is not None:
                    out[key] = str(row.value_json).strip().lower() == "true"
            return out

    def set_settings(self, data: dict[str, Any]) -> dict[str, bool]:
        with self.database.session() as db:
            for key in DEFAULT_SETTINGS:
                if key not in data:
                    continue
                row = db.get(AppSetting, key)
                stored = "true" if bool(data[key]) else "false"
                if row is None:
                    db.add(AppSetting(key=key, value_json=stored))
                else:
                    row.value_json = stored
                    row.updated_at = utcnow()
        return self.settings()

    def runtime_status(self) -> dict[str, Any]:
        return {
            "settings": self.settings(),
            "ocr": self.ocr.status(),
            "semantic_model": self.semantic.status(),
            "extractor": {"name": "rules", "version": "1", "ready": True},
        }

    def _message_media(self, message: ChatMessage) -> dict[str, Any]:
        return loads(message.media_json, {}) or {}

    def _content_fingerprint(self, message: ChatMessage) -> str:
        raw = dumps({"text": message.text or "", "message_type": message.message_type, "media": self._message_media(message)})
        return sha256(raw.encode("utf-8")).hexdigest()

    def _media_path(self, message: ChatMessage) -> Path | None:
        url = str(self._message_media(message).get("local_url") or "")
        if not url.startswith("/archive/"):
            return None
        path = (self.data_dir / url[len("/archive/"):]).resolve()
        try:
            path.relative_to(self.data_dir.resolve())
        except ValueError:
            return None
        return path if path.is_file() else None

    def _ensure_derived(self, db, message: ChatMessage, *, run_ocr: bool = False) -> ChatMessageDerived:
        fingerprint = self._content_fingerprint(message)
        row = db.get(ChatMessageDerived, message.message_id)
        if row is None:
            row = ChatMessageDerived(message_id=message.message_id, content_fingerprint=fingerprint)
            db.add(row)
            db.flush()
        elif row.content_fingerprint != fingerprint:
            row.content_fingerprint = fingerprint
            row.ocr_status = "PENDING" if message.message_type == "IMAGE" else "NOT_NEEDED"
            row.ocr_text = None
            row.ocr_engine = None
            row.ocr_engine_version = None
            row.ocr_confidence = None
            row.price_signal_score = 0
            row.price_signal_reasons_json = None
            row.updated_at = utcnow()

        if message.message_type == "IMAGE" and row.ocr_status in {"NOT_NEEDED", "PENDING"}:
            row.ocr_status = "PENDING"
        if message.message_type != "IMAGE" and row.ocr_status != "NOT_NEEDED":
            row.ocr_status = "NOT_NEEDED"

        settings = self.settings()
        if run_ocr and message.message_type == "IMAGE" and settings[SETTING_OCR] and row.ocr_status != "DONE":
            path = self._media_path(message)
            if not path:
                row.ocr_status = "NO_LOCAL_MEDIA"
            elif not self.ocr.available():
                row.ocr_status = "UNAVAILABLE"
                row.ocr_engine = self.ocr.name
                row.ocr_engine_version = self.ocr.version
            else:
                try:
                    result = self.ocr.extract(path)
                    row.ocr_text = result.text or None
                    row.ocr_confidence = result.confidence
                    row.ocr_engine = result.engine
                    row.ocr_engine_version = result.engine_version
                    row.ocr_status = "DONE"
                except Exception as exc:
                    LOGGER.warning("OCR failed message_id=%s error=%s", message.message_id, exc)
                    row.ocr_status = "ERROR"
            row.updated_at = utcnow()
        row.analyzed_at = utcnow()
        return row

    @staticmethod
    def _effective_text(message: ChatMessage, derived: ChatMessageDerived | None) -> str:
        if message.message_type == "IMAGE" and derived and derived.ocr_text:
            return derived.ocr_text
        return message.text or ""

    def signal(self, thread_id: int, *, allow_ocr: bool = True) -> dict[str, Any]:
        settings = self.settings()
        if not settings[SETTING_ENABLED]:
            return {"thread_id": thread_id, "enabled": False, "strong": False, "score": 0, "reasons": []}
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            if thread.person_id is None:
                return {"thread_id": thread_id, "enabled": True, "strong": False, "score": 0, "reasons": ["unassigned"]}
            rows = db.scalars(
                select(ChatMessage)
                .where(ChatMessage.thread_id == thread.thread_id, ChatMessage.direction.in_(["IN", "OUT"]))
                .order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id))
                .limit(12)
            ).all()
            rows.reverse()
            best = None
            previous_outgoing = None
            contains_image = False
            for message in rows:
                derived = self._ensure_derived(db, message, run_ocr=allow_ocr and message.message_type == "IMAGE")
                text = self._effective_text(message, derived)
                if message.direction == "OUT":
                    previous_outgoing = text
                    continue
                contains_image = contains_image or message.message_type == "IMAGE"
                result = detect_price_signal(text, previous_outgoing=previous_outgoing, has_image=message.message_type == "IMAGE")
                derived.price_signal_score = result.score
                derived.price_signal_reasons_json = dumps(list(result.reasons))
                derived.updated_at = utcnow()
                candidate = (result.score, message.message_id, list(result.reasons), derived.ocr_status)
                if best is None or candidate[0] >= best[0]:
                    best = candidate
            score, seed, reasons, ocr_status = best or (0, None, [], None)
            return {
                "thread_id": thread.thread_id,
                "person_id": thread.person_id,
                "enabled": True,
                "magic_button_enabled": settings[SETTING_MAGIC],
                "strong": bool(score >= 7),
                "score": int(score),
                "reasons": reasons,
                "seed_message_id": seed,
                "contains_image": contains_image,
                "ocr_status": ocr_status,
                "ocr_available": self.ocr.available(),
            }

    def _context_rows(self, db, thread: ChatThread, *, limit: int = 40) -> list[ContextMessage]:
        rows = db.scalars(
            select(ChatMessage)
            .where(ChatMessage.thread_id == thread.thread_id)
            .order_by(desc(ChatMessage.timestamp), desc(ChatMessage.message_id))
            .limit(limit)
        ).all()
        rows.reverse()
        out = []
        for message in rows:
            derived = self._ensure_derived(db, message, run_ocr=message.message_type == "IMAGE")
            out.append(ContextMessage(
                message_id=message.message_id,
                direction=message.direction,
                message_type=message.message_type,
                text=message.text or "",
                timestamp=message.timestamp,
                ocr_text=derived.ocr_text,
            ))
        return out

    def create_proposal(self, thread_id: int, *, trigger_type: str = "MANUAL", seed_message_id: int | None = None) -> dict[str, Any]:
        if not self.settings()[SETTING_ENABLED]:
            raise ValueError("Preiserkennung ist deaktiviert")
        if seed_message_id is None:
            signal = self.signal(thread_id)
            seed_message_id = signal.get("seed_message_id")
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
            current_id, current = self._current_proposal(db, int(thread.person_id))
            proposal = extract_with_rules(context, has_existing_price_list=current_id is not None)
            if not (proposal["offers"] or proposal["service_rules"] or proposal["conditions"]):
                raise ValueError("In diesem Chatabschnitt wurden keine ausreichend strukturierten Preisangaben erkannt")

            extraction = PriceExtraction(
                person_id=thread.person_id,
                thread_id=thread.thread_id,
                status="PENDING",
                trigger_type=str(trigger_type or "MANUAL").upper()[:24],
                schema_version=proposal["schema_version"],
                extractor_name="rules",
                extractor_version="1",
                original_proposal_json=dumps(proposal),
                context_fingerprint=context.fingerprint,
                base_price_list_id=current_id,
            )
            db.add(extraction)
            db.flush()
            for position, message in enumerate(context.messages):
                role = "PRIMARY" if message.message_id == seed_message_id else "IMAGE_OCR" if message.message_type == "IMAGE" and message.ocr_text else "QUESTION" if message.direction == "OUT" else "CONTEXT"
                db.add(PriceExtractionMessage(
                    price_extraction_id=extraction.price_extraction_id,
                    message_id=message.message_id,
                    role=role,
                    position=position,
                ))
            db.flush()
            return self._extraction_payload(db, extraction, current=current)

    def proposal(self, extraction_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            row = db.get(PriceExtraction, int(extraction_id))
            if row is None:
                raise KeyError(extraction_id)
            current = self._proposal_from_price_list(db, db.get(PriceList, row.base_price_list_id)) if row.base_price_list_id else None
            return self._extraction_payload(db, row, current=current)

    def reject(self, extraction_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            row = db.get(PriceExtraction, int(extraction_id))
            if row is None:
                raise KeyError(extraction_id)
            if row.status != "PENDING":
                raise ValueError("Vorschlag wurde bereits bearbeitet")
            row.status = "REJECTED"
            row.reviewed_at = utcnow()
            return {"price_extraction_id": row.price_extraction_id, "status": row.status}

    def accept(self, extraction_id: int, data: dict[str, Any]) -> dict[str, Any]:
        with self.database.session() as db:
            extraction = db.get(PriceExtraction, int(extraction_id))
            if extraction is None:
                raise KeyError(extraction_id)
            if extraction.status != "PENDING":
                raise ValueError("Vorschlag wurde bereits bearbeitet")
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
            fingerprint = self._proposal_fingerprint(effective)

            if extraction.base_price_list_id:
                current_meta = db.get(PriceListRevisionMeta, extraction.base_price_list_id)
                current_fp = current_meta.fingerprint if current_meta else self._proposal_fingerprint(current or empty_proposal())
                if current_fp == fingerprint:
                    extraction.status = "ACCEPTED_NO_CHANGE"
                    extraction.reviewed_proposal_json = dumps(reviewed)
                    extraction.reviewed_at = utcnow()
                    extraction.accepted_price_list_id = extraction.base_price_list_id
                    return self._extraction_payload(db, extraction, current=current)

            price_list = PriceList(
                person_id=int(extraction.person_id),
                observed_at=utcnow(),
                source="WHATSAPP_EXTRACTED",
                currency=effective["currency"],
                note=self._summary_note(effective),
            )
            db.add(price_list)
            db.flush()

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
                db.add(item); db.flush()
                db.add(PriceOfferMeta(
                    price_item_id=item.price_item_id,
                    duration_max_minutes=offer.get("duration_max_minutes"),
                    amount_type=offer.get("amount_type") or "EXACT",
                    amount_max_cents=offer.get("amount_max_cents"),
                ))
                self._save_evidence(db, price_list.price_list_id, extraction.price_extraction_id, offer.get("evidence"), price_item_id=item.price_item_id, user_edited=user_edited)

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
                db.add(row); db.flush()
                self._save_evidence(db, price_list.price_list_id, extraction.price_extraction_id, rule.get("evidence"), price_service_rule_id=row.price_service_rule_id, user_edited=user_edited)

            for position, condition in enumerate(effective["conditions"]):
                row = PriceCondition(
                    price_list_id=price_list.price_list_id,
                    condition_type=condition["condition_type"],
                    label=condition["label"],
                    amount_cents=condition.get("amount_cents"),
                    note=condition.get("note"),
                    position=position,
                )
                db.add(row); db.flush()
                self._save_evidence(db, price_list.price_list_id, extraction.price_extraction_id, condition.get("evidence"), price_condition_id=row.price_condition_id, user_edited=user_edited)

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

    def current(self, person_id: int) -> dict[str, Any]:
        with self.database.session() as db:
            if db.get(Person, int(person_id)) is None:
                raise KeyError(person_id)
            price_list_id, proposal = self._current_proposal(db, int(person_id))
            return {"person_id": int(person_id), "price_list_id": price_list_id, "proposal": proposal}

    def history(self, person_id: int, limit: int = 30) -> list[dict[str, Any]]:
        with self.database.session() as db:
            rows = db.scalars(
                select(PriceList)
                .where(PriceList.person_id == int(person_id))
                .order_by(desc(PriceList.observed_at), desc(PriceList.price_list_id))
                .limit(max(1, min(100, int(limit))))
            ).all()
            return [self._price_list_payload(db, row) for row in rows]

    def _current_proposal(self, db, person_id: int) -> tuple[int | None, dict[str, Any] | None]:
        row = db.scalar(
            select(PriceList)
            .where(PriceList.person_id == int(person_id))
            .order_by(desc(PriceList.observed_at), desc(PriceList.price_list_id))
            .limit(1)
        )
        return (row.price_list_id, self._proposal_from_price_list(db, row)) if row else (None, None)

    def _proposal_from_price_list(self, db, row: PriceList | None) -> dict[str, Any] | None:
        if row is None:
            return None
        proposal = empty_proposal()
        proposal["currency"] = row.currency
        proposal["update_mode"] = "FULL"
        items = db.scalars(select(PriceItem).where(PriceItem.price_list_id == row.price_list_id).order_by(PriceItem.price_item_id)).all()
        for index, item in enumerate(items, start=1):
            meta = db.get(PriceOfferMeta, item.price_item_id)
            proposal["offers"].append({
                "proposal_key": f"offer_{index}",
                "duration_minutes": item.duration_minutes,
                "duration_max_minutes": meta.duration_max_minutes if meta else None,
                "amount_type": meta.amount_type if meta else "EXACT",
                "amount_cents": item.amount_cents,
                "amount_max_cents": meta.amount_max_cents if meta else None,
                "label": item.label or item.service or item.item_type,
                "evidence": [],
            })
        rules = db.scalars(select(PriceServiceRule).where(PriceServiceRule.price_list_id == row.price_list_id).order_by(PriceServiceRule.position)).all()
        proposal["service_rules"] = [{
            "proposal_key": f"service_{i+1}", "service_key": x.service_key, "raw_label": x.raw_label,
            "status": x.status, "amount_cents": x.amount_cents, "note": x.note, "evidence": [],
        } for i, x in enumerate(rules)]
        conditions = db.scalars(select(PriceCondition).where(PriceCondition.price_list_id == row.price_list_id).order_by(PriceCondition.position)).all()
        proposal["conditions"] = [{
            "proposal_key": f"condition_{i+1}", "condition_type": x.condition_type, "label": x.label,
            "amount_cents": x.amount_cents, "note": x.note, "evidence": [],
        } for i, x in enumerate(conditions)]
        return validate_proposal(proposal)

    def _price_list_payload(self, db, row: PriceList) -> dict[str, Any]:
        meta = db.get(PriceListRevisionMeta, row.price_list_id)
        return {
            "price_list_id": row.price_list_id,
            "person_id": row.person_id,
            "observed_at": iso(row.observed_at),
            "source": row.source,
            "currency": row.currency,
            "note": row.note,
            "revision": {
                "supersedes_price_list_id": meta.supersedes_price_list_id,
                "source_extraction_id": meta.source_extraction_id,
                "revision_type": meta.revision_type,
                "completeness": meta.completeness,
                "fingerprint": meta.fingerprint,
                "confirmed_at": iso(meta.confirmed_at),
            } if meta else None,
            "proposal": self._proposal_from_price_list(db, row),
        }

    def _extraction_payload(self, db, row: PriceExtraction, *, current: dict[str, Any] | None = None) -> dict[str, Any]:
        original = validate_proposal(loads(row.original_proposal_json, {}))
        reviewed = validate_proposal(loads(row.reviewed_proposal_json, {})) if row.reviewed_proposal_json else None
        proposal = reviewed or original
        effective = merge_partial(current, proposal) if current and proposal["update_mode"] in {"PARTIAL", "UNKNOWN"} else validate_proposal(proposal)
        messages = db.scalars(
            select(PriceExtractionMessage)
            .where(PriceExtractionMessage.price_extraction_id == row.price_extraction_id)
            .order_by(PriceExtractionMessage.position)
        ).all()
        return {
            "price_extraction_id": row.price_extraction_id,
            "person_id": row.person_id,
            "thread_id": row.thread_id,
            "status": row.status,
            "trigger_type": row.trigger_type,
            "extractor": {"name": row.extractor_name, "version": row.extractor_version},
            "base_price_list_id": row.base_price_list_id,
            "accepted_price_list_id": row.accepted_price_list_id,
            "created_at": iso(row.created_at),
            "reviewed_at": iso(row.reviewed_at),
            "proposal": proposal,
            "effective_proposal": effective,
            "diff": proposal_diff(current, effective),
            "messages": [{"message_id": x.message_id, "role": x.role, "position": x.position} for x in messages],
        }

    def _validate_grounding(self, db, extraction: PriceExtraction, proposal: dict[str, Any]) -> None:
        allowed = set(db.scalars(
            select(PriceExtractionMessage.message_id).where(
                PriceExtractionMessage.price_extraction_id == extraction.price_extraction_id
            )
        ).all())
        cache: dict[int, str] = {}
        for section in (proposal["offers"], proposal["service_rules"], proposal["conditions"]):
            for item in section:
                for evidence in item.get("evidence") or []:
                    message_id = int(evidence["message_id"])
                    if message_id not in allowed:
                        raise ValueError("Proposal verweist auf eine fremde Nachricht")
                    if message_id not in cache:
                        message = db.get(ChatMessage, message_id)
                        derived = db.get(ChatMessageDerived, message_id)
                        cache[message_id] = self._effective_text(message, derived) if message else ""
        for offer in proposal["offers"]:
            evidence_text = "\n".join(cache.get(int(e["message_id"]), "") for e in offer.get("evidence") or [])
            if offer.get("duration_minutes") is not None:
                if offer["duration_minutes"] not in {x["minutes"] for x in extract_durations(evidence_text)}:
                    raise ValueError("Erkannte Dauer ist nicht durch die Quellnachricht belegt")
            if offer.get("amount_cents") is not None:
                if offer["amount_cents"] not in {x["amount_cents"] for x in extract_money(evidence_text)}:
                    raise ValueError("Erkannter Preis ist nicht durch die Quellnachricht belegt")
        for rule in proposal["service_rules"]:
            if rule.get("amount_cents") is not None:
                evidence_text = "\n".join(cache.get(int(e["message_id"]), "") for e in rule.get("evidence") or [])
                if rule["amount_cents"] not in {x["amount_cents"] for x in extract_money(evidence_text)}:
                    raise ValueError("Erkannter Aufpreis ist nicht durch die Quellnachricht belegt")

    def _save_evidence(self, db, price_list_id: int, extraction_id: int, evidence_rows: list[dict[str, Any]] | None,
                       *, price_item_id: int | None = None, price_service_rule_id: int | None = None,
                       price_condition_id: int | None = None, user_edited: bool = False) -> None:
        rows = evidence_rows or []
        if not rows and user_edited:
            db.add(PriceEvidence(
                price_list_id=price_list_id, price_item_id=price_item_id,
                price_service_rule_id=price_service_rule_id, price_condition_id=price_condition_id,
                extraction_id=extraction_id, source_type="USER_EDIT", excerpt="Manuell im Preisvorschlag ergänzt oder geändert",
            ))
            return
        for evidence in rows:
            db.add(PriceEvidence(
                price_list_id=price_list_id,
                price_item_id=price_item_id,
                price_service_rule_id=price_service_rule_id,
                price_condition_id=price_condition_id,
                message_id=int(evidence["message_id"]) if evidence.get("message_id") else None,
                extraction_id=extraction_id,
                source_type="USER_EDIT" if user_edited else str(evidence.get("source_type") or "CHAT_TEXT")[:32],
                excerpt=str(evidence.get("excerpt") or "")[:500] or None,
            ))

    @staticmethod
    def _proposal_fingerprint(proposal: dict[str, Any]) -> str:
        value = validate_proposal(proposal)
        canonical = {
            "currency": value["currency"],
            "offers": [{k: x.get(k) for k in ("duration_minutes", "duration_max_minutes", "amount_type", "amount_cents", "amount_max_cents", "label")} for x in value["offers"]],
            "service_rules": [{k: x.get(k) for k in ("service_key", "raw_label", "status", "amount_cents", "note")} for x in value["service_rules"]],
            "conditions": [{k: x.get(k) for k in ("condition_type", "label", "amount_cents", "note")} for x in value["conditions"]],
        }
        return sha256(dumps(canonical).encode("utf-8")).hexdigest()

    @staticmethod
    def _summary_note(proposal: dict[str, Any]) -> str | None:
        parts = []
        for rule in proposal.get("service_rules") or []:
            if rule["status"] == "INCLUDED": parts.append(f"{rule['raw_label']} inklusive")
            elif rule["status"] == "SURCHARGE" and rule.get("amount_cents") is not None: parts.append(f"{rule['raw_label']} +{rule['amount_cents']/100:g} €")
            elif rule["status"] == "EXCLUDED": parts.append(f"{rule['raw_label']} nicht angeboten")
            elif rule["status"] == "ON_REQUEST": parts.append(f"{rule['raw_label']} auf Anfrage")
        for condition in proposal.get("conditions") or []:
            if condition.get("amount_cents") is not None:
                parts.append(f"{condition['label']} +{condition['amount_cents']/100:g} €")
        return " · ".join(parts)[:4000] or None

    def reconcile_identity_events(self) -> int:
        moved = 0
        with self.database.session() as db:
            events = db.scalars(select(IdentityEvent).where(IdentityEvent.previous_person_id.is_not(None)).order_by(IdentityEvent.identity_event_id)).all()
            for event in events:
                if event.previous_person_id == event.new_person_id:
                    continue
                result = db.execute(
                    update(PriceExtraction)
                    .where(PriceExtraction.person_id == event.previous_person_id)
                    .values(person_id=event.new_person_id)
                )
                moved += int(result.rowcount or 0)
        return moved
