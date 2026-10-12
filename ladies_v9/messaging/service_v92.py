from __future__ import annotations

import base64
import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select

from ladies_v9.database import Database
from ladies_v9.models import AppSetting, Listing, Person, PersonIdentifier, utcnow
from .integration import install_identity_merge_listener
from .service import EventHub, MessagingService as BaseMessagingService
from .whatsapp_manager import BaileysWorkerManager

LOGGER = logging.getLogger("ladies_explorer.messaging")
TYPING_SETTING_KEY = "whatsapp_typing_enabled"


class MessagingService(BaseMessagingService):
    """V9.2 lifecycle refinements on top of the domain service.

    Fresh installs remain NOT_CONFIGURED until the user explicitly chooses
    "WhatsApp verbinden". Existing sessions auto-start. The local message store
    is independent from the Baileys auth/session lifecycle.
    """

    def __init__(self, db_path: str | Path, data_dir: str | Path):
        install_identity_merge_listener()
        self.database = Database(db_path)
        from . import models as _messaging_models  # noqa: F401
        self.database.create_schema()
        self.events = EventHub()
        with self.database.session() as db:
            setting = db.get(AppSetting, TYPING_SETTING_KEY)
            self.typing_enabled = setting is None or setting.value_json.strip().lower() != "false"
        self.data_dir = Path(data_dir)
        self.auth_dir = self.data_dir / "whatsapp" / "auth"
        self.media_dir = self.data_dir / "whatsapp" / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        root = Path(__file__).resolve().parent
        self.worker = BaileysWorkerManager(
            root / "whatsapp_worker",
            self.auth_dir,
            self._worker_event,
        )
        ok, detail = self.worker.dependency_status()
        if not ok:
            self.worker.state = "DEPENDENCY_MISSING"
            self.worker.detail = detail
        elif (self.auth_dir / "creds.json").exists():
            self.worker.start()
        else:
            self.worker.state = "NOT_CONFIGURED"

    def set_typing_enabled(self, value: bool) -> None:
        self.typing_enabled = bool(value)
        with self.database.session() as db:
            row = db.get(AppSetting, TYPING_SETTING_KEY)
            stored = "true" if self.typing_enabled else "false"
            if row is None:
                db.add(AppSetting(key=TYPING_SETTING_KEY, value_json=stored))
            else:
                row.value_json = stored
                row.updated_at = utcnow()

    def connect(self) -> dict[str, Any]:
        ok, detail = self.worker.dependency_status()
        if not ok:
            self.worker.state = "DEPENDENCY_MISSING"
            self.worker.detail = detail
            return self.status()
        try:
            if self.worker.proc and self.worker.proc.poll() is None:
                self.worker.command("connect", timeout=15)
            else:
                self.worker.start()
        except Exception as exc:
            LOGGER.warning("WhatsApp connect failed: %s", exc)
        return self.status()

    def logout(self) -> dict[str, Any]:
        # Transport/session reset only. ChatThread/ChatMessage are intentionally
        # untouched and remain the Ladies-owned history.
        try:
            if self.worker.proc and self.worker.proc.poll() is None:
                self.worker.command("logout", timeout=10)
        except Exception:
            LOGGER.exception("WhatsApp logout failed")
        try:
            shutil.rmtree(self.auth_dir, ignore_errors=True)
            self.auth_dir.mkdir(parents=True, exist_ok=True)
        finally:
            self.worker.qr = None
            self.worker.account = None
            self.worker.state = "LOGGED_OUT"
        return self.status()

    def _ingest_message(self, payload: dict[str, Any]) -> None:
        media = payload.get("media")
        if isinstance(media, dict) and media.get("data_base64"):
            try:
                raw = base64.b64decode(media["data_base64"], validate=True)
                digest = hashlib.sha256(raw).hexdigest()
                mime = str(media.get("mime_type") or "application/octet-stream").lower()
                ext = ".jpg" if "jpeg" in mime or "jpg" in mime else ".png" if "png" in mime else ".webp" if "webp" in mime else ".bin"
                path = self.media_dir / f"{digest}{ext}"
                if not path.exists():
                    path.write_bytes(raw)
                clean_media = {k: v for k, v in media.items() if k != "data_base64"}
                clean_media["local_url"] = f"/archive/whatsapp/media/{path.name}"
                clean_media["byte_size"] = len(raw)
                payload = dict(payload)
                payload["media"] = clean_media
            except Exception:
                LOGGER.exception("Incoming WhatsApp media could not be archived")
        super()._ingest_message(payload)

    def assignment_candidates(self, endpoint_id: int) -> list[dict[str, Any]]:
        """Expose real evidence and clearly mark manual choices as such."""
        from .models import ContactEndpoint
        with self.database.session() as db:
            endpoint = db.get(ContactEndpoint, int(endpoint_id))
            if endpoint is None:
                raise KeyError(endpoint_id)
            exact_ids = set(db.scalars(
                select(PersonIdentifier.person_id).where(
                    PersonIdentifier.kind == "PHONE",
                    PersonIdentifier.normalized_value == endpoint.normalized_phone,
                )
            ).all())
            persons = db.scalars(select(Person).order_by(desc(Person.last_seen_at)).limit(50)).all()
            out: list[dict[str, Any]] = []
            for person in persons:
                evidence = []
                if person.person_id in exact_ids:
                    evidence.append({
                        "kind": "PHONE",
                        "decision": "MATCH",
                        "text": "Gleiche normalisierte Telefonnummer",
                    })
                else:
                    listing = db.scalar(
                        select(Listing)
                        .where(Listing.person_id == person.person_id)
                        .order_by(desc(Listing.last_seen_at))
                        .limit(1)
                    )
                    if listing and listing.match_decision in {"STRONG_CANDIDATE", "CANDIDATE"}:
                        evidence.append({
                            "kind": "IDENTITY_CONTEXT",
                            "decision": listing.match_decision,
                            "text": listing.match_reason or "Vorhandene Identity-Evidence der Person",
                        })
                    else:
                        evidence.append({
                            "kind": "MANUAL",
                            "decision": "NO_EVIDENCE",
                            "text": "Keine automatische Evidence für diese Nummer – nur manuelle Auswahl",
                        })
                out.append({
                    "person_id": person.person_id,
                    "display_name": person.display_name,
                    "evidence": evidence,
                    "exact_phone_match": person.person_id in exact_ids,
                })
            out.sort(key=lambda x: (not x["exact_phone_match"], x["display_name"] or ""))
            return out
