from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, BinaryIO

import imageio_ffmpeg
from sqlalchemy import select, update

from ladies_v9.models import utcnow
from .models import ChatMessage, ChatMessageMeta, ChatThread, ContactEndpoint
from .service_complete import MessagingService as CompleteMessagingService, _load

LOGGER = logging.getLogger("ladies_explorer.messaging.reviewfix")
SAFE_INLINE_MIMES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "video/mp4",
    "video/webm",
    "audio/ogg",
    "audio/webm",
    "audio/mpeg",
    "audio/mp4",
}
LEGACY_MEDIA_PREFIX = "/archive/whatsapp/media/"


def _archive_name(media: dict[str, Any]) -> str:
    raw = str(media.get("archive_name") or "")
    if not raw:
        local_url = str(media.get("local_url") or "")
        if local_url.startswith(LEGACY_MEDIA_PREFIX):
            raw = local_url[len(LEGACY_MEDIA_PREFIX):]
    return Path(raw).name if raw else ""


class MessagingService(CompleteMessagingService):
    """Small hardening layer for the reviewed V9.3 messaging feature set."""

    def __init__(self, db_path: str | Path, data_dir: str | Path):
        super().__init__(db_path, data_dir)
        self.view_once_dir = Path(data_dir) / "whatsapp" / "view-once"
        self.view_once_dir.mkdir(parents=True, exist_ok=True)

    def _archive_incoming_media(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload = super()._archive_incoming_media(payload)
        if not payload.get("view_once"):
            return payload
        media = payload.get("media")
        if not isinstance(media, dict) or not media.get("archive_name"):
            return payload
        name = Path(str(media["archive_name"])).name
        target = (self.view_once_dir / name).resolve()
        try:
            target.relative_to(self.view_once_dir.resolve())
        except ValueError:
            LOGGER.warning("Rejected view-once path outside private store: %s", name)
            return payload
        if target.exists():
            clean = dict(media)
            clean["archive_name"] = name
            clean["private_view_once"] = True
            clean.pop("local_url", None)
            payload = dict(payload)
            payload["media"] = clean
        return payload

    @staticmethod
    def _resolved_endpoint(db, thread: ChatThread, endpoint_id: int | None, chooser) -> ContactEndpoint:
        endpoint = db.get(ContactEndpoint, int(endpoint_id)) if endpoint_id else chooser(db, thread.person_id)
        if endpoint is None or thread.person_id is None or endpoint.person_id != thread.person_id or endpoint.state != "ACTIVE":
            raise ValueError("Kein gültiger WhatsApp-Endpunkt ausgewählt")
        return endpoint

    @staticmethod
    def _validate_reply_target(db, thread: ChatThread, endpoint: ContactEndpoint, reply_to: int | None) -> None:
        if reply_to is None:
            return
        quoted = db.get(ChatMessage, int(reply_to))
        if quoted is None or quoted.thread_id != thread.thread_id:
            raise ValueError("Antwort-Ziel gehört nicht zu diesem Chat")
        if quoted.endpoint_id != endpoint.endpoint_id:
            raise ValueError("Antworten sind nur innerhalb derselben WhatsApp-Nummer möglich")

    def _quote_command(self, db, message: ChatMessage) -> dict[str, Any] | None:
        if not message.reply_to_message_id:
            return None
        quoted = db.get(ChatMessage, int(message.reply_to_message_id))
        if quoted is None or quoted.thread_id != message.thread_id:
            raise ValueError("Antwort-Ziel gehört nicht zu diesem Chat")
        if quoted.endpoint_id != message.endpoint_id:
            raise ValueError("Antworten sind nur innerhalb derselben WhatsApp-Nummer möglich")
        return super()._quote_command(db, message)

    def send_text(self, thread_id: int, text: str, endpoint_id: int | None = None, reply_to: int | None = None):
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            endpoint = self._resolved_endpoint(db, thread, endpoint_id, self._choose_endpoint)
            self._validate_reply_target(db, thread, endpoint, reply_to)
            resolved_endpoint_id = int(endpoint.endpoint_id)
        return super().send_text(thread_id, text, resolved_endpoint_id, reply_to)

    def _normalize_voice_file(self, archive_name: str) -> tuple[str, str]:
        source = (self.media_dir / Path(archive_name).name).resolve()
        if not source.exists():
            raise ValueError("Sprachaufnahme fehlt")
        output = self.media_dir / f"voice-{uuid.uuid4().hex}.ogg"
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-c:a",
            "libopus",
            "-b:a",
            "32k",
            "-vbr",
            "on",
            "-application",
            "voip",
            "-f",
            "ogg",
            str(output),
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
            if completed.returncode != 0 or not output.exists() or output.stat().st_size == 0:
                raise ValueError((completed.stderr or "Sprachaufnahme konnte nicht in Opus/OGG umgewandelt werden")[-500:])
            source.unlink(missing_ok=True)
            return output.name, "audio/ogg; codecs=opus"
        except Exception:
            output.unlink(missing_ok=True)
            source.unlink(missing_ok=True)
            raise

    def send_media(
        self,
        thread_id: int,
        *,
        archive_name: str,
        media_type: str,
        mime_type: str | None = None,
        file_name: str | None = None,
        caption: str | None = None,
        endpoint_id: int | None = None,
        reply_to: int | None = None,
        view_once: bool = False,
    ):
        media_type = str(media_type or "").upper()
        with self.database.session() as db:
            thread = db.get(ChatThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            endpoint = self._resolved_endpoint(db, thread, endpoint_id, self._choose_endpoint)
            self._validate_reply_target(db, thread, endpoint, reply_to)
            resolved_endpoint_id = int(endpoint.endpoint_id)
        if media_type == "VOICE":
            archive_name, mime_type = self._normalize_voice_file(archive_name)
            file_name = Path(file_name or "sprachnachricht.ogg").stem + ".ogg"
        return super().send_media(
            thread_id,
            archive_name=archive_name,
            media_type=media_type,
            mime_type=mime_type,
            file_name=file_name,
            caption=caption,
            endpoint_id=resolved_endpoint_id,
            reply_to=reply_to,
            view_once=view_once,
        )

    def _stored_media_path(self, message: ChatMessage, media: dict[str, Any]) -> Path:
        name = _archive_name(media)
        if not name:
            raise FileNotFoundError(name)
        meta = message.extra
        private = bool(meta and meta.view_once) or bool(media.get("private_view_once"))
        root = self.view_once_dir if private else self.media_dir
        target = (root / name).resolve()
        target.relative_to(root.resolve())
        if not target.exists() and private:
            legacy = (self.media_dir / name).resolve()
            legacy.relative_to(self.media_dir.resolve())
            if legacy.exists():
                target = legacy
        if not target.exists():
            raise FileNotFoundError(name)
        return target

    def media_path(self, message_id: int) -> tuple[Path, str, str, bool]:
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None:
                raise KeyError(message_id)
            meta = self._ensure_meta(db, message)
            if meta.view_once:
                raise ValueError("Einmal-Medien werden nur über den Einmal-Endpunkt ausgeliefert")
            if meta.deleted_at is not None:
                raise FileNotFoundError(str(message_id))
            media = _load(message.media_json, {}) or {}
            target = self._stored_media_path(message, media)
            original_mime = str(media.get("mime_type") or "application/octet-stream").split(";", 1)[0].strip().lower()
            inline = original_mime in SAFE_INLINE_MIMES and message.message_type != "DOCUMENT"
            mime = original_mime if inline else "application/octet-stream"
            name = Path(str(media.get("file_name") or target.name)).name
            return target, mime, name, not inline

    def view_once_path(self, message_id: int) -> tuple[BinaryIO, str]:
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None:
                raise KeyError(message_id)
            meta = self._ensure_meta(db, message)
            if not meta.view_once:
                raise ValueError("Diese Nachricht ist kein Einmal-Medium")
            if message.direction != "IN":
                raise ValueError("Gesendete Einmal-Medien können nicht erneut geöffnet werden")
            if meta.view_once_consumed_at is not None:
                raise RuntimeError("Einmal-Medium wurde bereits geöffnet")
            media = _load(message.media_json, {}) or {}
            source = self._stored_media_path(message, media)
            mime = str(media.get("mime_type") or "application/octet-stream")

        spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
        try:
            with source.open("rb") as handle:
                shutil.copyfileobj(handle, spool, length=1024 * 1024)
            spool.seek(0)
            now = utcnow()
            with self.database.session() as db:
                result = db.execute(
                    update(ChatMessageMeta)
                    .where(
                        ChatMessageMeta.message_id == int(message_id),
                        ChatMessageMeta.view_once_consumed_at.is_(None),
                    )
                    .values(view_once_consumed_at=now, updated_at=now)
                )
                if result.rowcount != 1:
                    raise RuntimeError("Einmal-Medium wurde bereits geöffnet")
            source.unlink(missing_ok=True)
            return spool, mime
        except Exception:
            spool.close()
            raise

    def _delete_local_media(self, message: ChatMessage) -> None:
        media = _load(message.media_json, {}) or {}
        try:
            self._stored_media_path(message, media).unlink(missing_ok=True)
        except (FileNotFoundError, ValueError):
            pass

    def _ingest_content_update(self, payload: dict[str, Any]) -> None:
        provider_id = payload.get("provider_message_id")
        action = str(payload.get("action") or "").upper()
        if not provider_id or action not in {"EDIT", "DELETE"}:
            return
        browser_event = None
        with self.database.session() as db:
            message = db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == provider_id))
            if message is None:
                return
            meta = self._ensure_meta(db, message)
            now = utcnow()
            if action == "DELETE":
                self._delete_local_media(message)
                message.text = None
                message.media_json = None
                message.status = "DELETED"
                meta.deleted_at = now
            else:
                message.text = payload.get("text")
                meta.edited_at = now
            meta.updated_at = now
            browser_event = {
                "type": "message_update",
                "thread_id": message.thread_id,
                "message_id": message.message_id,
                "status": message.status,
            }
        if browser_event:
            self.events.publish(browser_event)

    def _worker_event(self, event: dict[str, Any]) -> None:
        if event.get("type") == "message_content_update":
            try:
                self._ingest_content_update(event.get("payload") or {})
            except Exception:
                LOGGER.exception("Failed to ingest WhatsApp edit/delete update")
            return
        return super()._worker_event(event)

    @staticmethod
    def _message_payload(message: ChatMessage | None) -> dict[str, Any] | None:
        payload = CompleteMessagingService._message_payload(message)
        if payload is None or message is None:
            return payload
        payload.pop("provider_key", None)
        media = payload.get("media")
        if isinstance(media, dict):
            has_stored_file = bool(_archive_name(media))
            clean = dict(media)
            for key in ("archive_name", "sha256", "private_view_once", "local_url"):
                clean.pop(key, None)
            if not payload.get("view_once") and has_stored_file and not payload.get("deleted_at"):
                clean["local_url"] = f"/api/messaging/messages/{message.message_id}/media"
            payload["media"] = clean or None
        return payload
