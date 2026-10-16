from __future__ import annotations

import base64
import hashlib
import logging
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, BinaryIO

import imageio_ffmpeg

LOGGER = logging.getLogger("ladies_explorer.messaging.media")
SAFE_INLINE_MIMES = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "video/mp4", "video/webm",
    "audio/ogg", "audio/webm", "audio/mpeg", "audio/mp4",
}
LEGACY_MEDIA_PREFIX = "/archive/whatsapp/media/"
VOICE_CONVERSION_ERROR = "Sprachnachricht konnte nicht verarbeitet werden"


class MessagingMediaStore:
    def __init__(self, data_dir: str | Path):
        root = Path(data_dir) / "whatsapp"
        self.media_dir = root / "media"
        self.view_once_dir = root / "view-once"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.view_once_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def archive_name(media: dict[str, Any]) -> str:
        raw = str(media.get("archive_name") or "")
        if not raw:
            local_url = str(media.get("local_url") or "")
            if local_url.startswith(LEGACY_MEDIA_PREFIX):
                raw = local_url[len(LEGACY_MEDIA_PREFIX):]
        return Path(raw).name if raw else ""

    @staticmethod
    def _extension(mime: str) -> str:
        mime = mime.lower()
        if "jpeg" in mime or "jpg" in mime:
            return ".jpg"
        if "png" in mime:
            return ".png"
        if "webp" in mime:
            return ".webp"
        return ".bin"

    def archive_incoming(self, payload: dict[str, Any]) -> dict[str, Any]:
        media = payload.get("media")
        if not isinstance(media, dict):
            return payload

        # Decode inline base64 media before archiving.
        if media.get("data_base64"):
            try:
                raw = base64.b64decode(media["data_base64"], validate=True)
                digest = hashlib.sha256(raw).hexdigest()
                mime = str(media.get("mime_type") or "application/octet-stream").lower()
                root = self.view_once_dir if payload.get("view_once") else self.media_dir
                target = root / f"{digest}{self._extension(mime)}"
                if not target.exists():
                    target.write_bytes(raw)
                clean = {key: value for key, value in media.items() if key != "data_base64"}
                clean["archive_name"] = target.name
                clean["byte_size"] = len(raw)
                if payload.get("view_once"):
                    clean["private_view_once"] = True
                    clean.pop("local_url", None)
                else:
                    clean["local_url"] = f"/archive/whatsapp/media/{target.name}"
                result = dict(payload)
                result["media"] = clean
                return result
            except Exception:
                LOGGER.exception("Incoming WhatsApp media could not be archived")
                return payload

        name = self.archive_name(media)
        if not name:
            return payload
        try:
            target = self.resolve(name, private=bool(payload.get("view_once")))
        except (FileNotFoundError, ValueError):
            # Keep the message when its media file is missing, but never expose an unchecked path.
            clean = dict(media)
            clean.pop("archive_name", None)
            clean.pop("local_url", None)
            result = dict(payload)
            result["media"] = clean
            return result
        clean = dict(media)
        clean["archive_name"] = target.name
        if payload.get("view_once"):
            clean["private_view_once"] = True
            clean.pop("local_url", None)
        else:
            clean["local_url"] = f"/archive/whatsapp/media/{target.name}"
        result = dict(payload)
        result["media"] = clean
        return result

    def resolve(self, archive_name: str, *, private: bool = False, allow_legacy_media: bool = False) -> Path:
        name = Path(str(archive_name or "")).name
        if not name:
            raise FileNotFoundError(name)
        root = self.view_once_dir if private else self.media_dir
        target = (root / name).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("Ungültiger Medienpfad") from exc
        if not target.exists() and private and allow_legacy_media:
            legacy = (self.media_dir / name).resolve()
            legacy.relative_to(self.media_dir.resolve())
            if legacy.exists():
                target = legacy
        if not target.is_file():
            raise FileNotFoundError(name)
        return target

    def upload_target(self, archive_name: str) -> Path:
        name = Path(str(archive_name or "")).name
        if not name:
            raise ValueError("Ungültiger Dateiname")
        target = (self.media_dir / name).resolve()
        target.relative_to(self.media_dir.resolve())
        return target

    def normalize_voice(self, archive_name: str) -> tuple[str, str]:
        source = self.resolve(archive_name)
        output = self.media_dir / f".voice-{uuid.uuid4().hex}.ogg"
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-vn", "-c:a", "libopus", "-b:a", "32k", "-vbr", "on",
            "-application", "voip", "-f", "ogg", str(output),
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
            if completed.returncode != 0 or not output.exists() or output.stat().st_size == 0:
                LOGGER.warning("Voice conversion rejected returncode=%s", completed.returncode)
                raise ValueError(VOICE_CONVERSION_ERROR)
            # Keep the archive name stable so every failure path can clean up the same target.
            output.replace(source)
            return source.name, "audio/ogg; codecs=opus"
        except ValueError:
            output.unlink(missing_ok=True)
            raise
        except Exception as exc:
            output.unlink(missing_ok=True)
            LOGGER.warning("Voice conversion failed error_type=%s", type(exc).__name__)
            raise ValueError(VOICE_CONVERSION_ERROR) from exc

    def delivery(self, message_type: str, media: dict[str, Any], *, private: bool = False) -> tuple[Path, str, str, bool]:
        target = self.resolve(self.archive_name(media), private=private, allow_legacy_media=private)
        original_mime = str(media.get("mime_type") or "application/octet-stream").split(";", 1)[0].strip().lower()
        inline = original_mime in SAFE_INLINE_MIMES and str(message_type).upper() != "DOCUMENT"
        mime = original_mime if inline else "application/octet-stream"
        name = Path(str(media.get("file_name") or target.name)).name
        return target, mime, name, not inline

    def spool_and_delete(self, source: Path) -> BinaryIO:
        spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
        try:
            with source.open("rb") as handle:
                shutil.copyfileobj(handle, spool, length=1024 * 1024)
            spool.seek(0)
            source.unlink(missing_ok=True)
            return spool
        except Exception:
            spool.close()
            raise

    def delete(self, media: dict[str, Any], *, private: bool = False) -> None:
        try:
            self.resolve(self.archive_name(media), private=private, allow_legacy_media=private).unlink(missing_ok=True)
        except (FileNotFoundError, ValueError):
            pass
