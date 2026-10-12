from __future__ import annotations

import gzip
import hashlib
import io
import logging
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import MediaReference
from .identity import hash_to_hex
from .models import MediaAsset, MediaSource, utcnow

LOGGER = logging.getLogger("ladies_explorer.media")


@dataclass(frozen=True)
class ArchivedMedia:
    asset: MediaAsset | None
    source: MediaSource
    remote_url: str
    remote_base_url: str
    revision_token: str | None
    etag: str | None
    last_modified: str | None


def remote_base_url(url: str) -> str:
    p = urlparse(url)
    return p._replace(query="", fragment="").geturl()


def dhash_image_bytes(data: bytes) -> int:
    with Image.open(io.BytesIO(data)) as image:
        image = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = list(image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata())
    value = 0
    for row in range(8):
        offset = row * 9
        for col in range(8):
            value = (value << 1) | int(pixels[offset + col] > pixels[offset + col + 1])
    return value


class ArchiveManager:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.raw_dir = self.data_dir / "raw_profiles"
        self.media_dir = self.data_dir / "media"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        LOGGER.info("Archive configured data_dir=%s", self.data_dir.resolve())

    def archive_html(self, raw_html: str) -> tuple[str, str]:
        raw = raw_html.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        target = self.raw_dir / digest[:2] / f"{digest}.html.gz"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            with gzip.open(tmp, "wb", compresslevel=9) as fh:
                fh.write(raw)
            tmp.replace(target)
        LOGGER.debug("Archived raw profile sha256=%s bytes=%s path=%s", digest, len(raw), target)
        return digest, str(target.relative_to(self.data_dir))

    @staticmethod
    def _extension(kind: str, mime_type: str | None, url: str) -> str:
        path_ext = Path(urlparse(url).path).suffix.lower()
        if path_ext and len(path_ext) <= 6:
            return path_ext
        guessed = mimetypes.guess_extension((mime_type or "").split(";",1)[0].strip())
        return guessed or (".bin" if kind != "IMAGE" else ".img")

    def _save_asset_bytes(self, data: bytes, *, kind: str, mime_type: str | None, url: str) -> tuple[str, str]:
        digest = hashlib.sha256(data).hexdigest()
        ext = self._extension(kind, mime_type, url)
        sub = "images" if kind == "IMAGE" else "videos" if kind == "VIDEO" else "other"
        target = self.media_dir / sub / digest[:2] / f"{digest}{ext}"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
        LOGGER.debug("Archived media kind=%s sha256=%s bytes=%s path=%s", kind, digest, len(data), target)
        return digest, str(target.relative_to(self.data_dir))

    def ensure_remote_media(
        self,
        db: Session,
        http: requests.Session,
        ref: MediaReference,
        *,
        download: bool = True,
        timeout: float = 30,
    ) -> ArchivedMedia:
        base = remote_base_url(ref.url)
        LOGGER.debug("Media ensure kind=%s download=%s url=%s", ref.kind, download, base)
        source = db.scalar(select(MediaSource).where(MediaSource.remote_base_url == base))
        if source is None:
            source = MediaSource(remote_base_url=base, last_remote_url=ref.url, revision_token=ref.revision_token)
            db.add(source)
            db.flush()

        same_revision = bool(
            source.media_asset_id
            and ref.revision_token
            and source.revision_token == ref.revision_token
        )
        if same_revision:
            source.last_remote_url = ref.url
            source.last_checked_at = utcnow()
            asset = db.get(MediaAsset, source.media_asset_id)
            if asset:
                asset.last_seen_at = utcnow()
            return ArchivedMedia(asset, source, ref.url, base, ref.revision_token, source.etag, source.last_modified)

        if not download:
            # For non-archived videos retain cheap revision metadata when the CDN
            # supports HEAD; failure is intentionally non-fatal.
            try:
                response = http.head(ref.url, headers={"Referer": "https://www.ladies.de/"}, timeout=timeout, allow_redirects=True)
                if response.ok:
                    source.etag = response.headers.get("ETag") or source.etag
                    source.last_modified = response.headers.get("Last-Modified") or source.last_modified
                    try:
                        source.content_length = int(response.headers.get("Content-Length")) if response.headers.get("Content-Length") else source.content_length
                    except ValueError:
                        pass
            except Exception:
                pass
            source.last_remote_url = ref.url
            source.revision_token = ref.revision_token or source.revision_token
            source.last_checked_at = utcnow()
            asset = db.get(MediaAsset, source.media_asset_id) if source.media_asset_id else None
            return ArchivedMedia(asset, source, ref.url, base, ref.revision_token, source.etag, source.last_modified)

        headers = {"Accept": "image/*" if ref.kind == "IMAGE" else "*/*", "Referer": "https://www.ladies.de/"}
        if source.etag:
            headers["If-None-Match"] = source.etag
        if source.last_modified:
            headers["If-Modified-Since"] = source.last_modified
        response = http.get(ref.url, headers=headers, timeout=timeout)
        if response.status_code == 304 and source.media_asset_id:
            asset = db.get(MediaAsset, source.media_asset_id)
            source.last_checked_at = utcnow()
            source.last_remote_url = ref.url
            source.revision_token = ref.revision_token or source.revision_token
            if asset:
                asset.last_seen_at = utcnow()
            return ArchivedMedia(asset, source, ref.url, base, ref.revision_token, source.etag, source.last_modified)
        response.raise_for_status()
        data = response.content
        mime_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip() or None
        digest, local_path = self._save_asset_bytes(data, kind=ref.kind, mime_type=mime_type, url=ref.url)
        asset = db.scalar(select(MediaAsset).where(MediaAsset.sha256 == digest))
        width = height = None
        dhash = None
        if ref.kind == "IMAGE":
            try:
                with Image.open(io.BytesIO(data)) as image:
                    width, height = image.size
                dhash = hash_to_hex(dhash_image_bytes(data))
            except Exception:
                pass
        if asset is None:
            asset = MediaAsset(
                sha256=digest,
                dhash=dhash,
                kind=ref.kind,
                mime_type=mime_type,
                byte_size=len(data),
                width=width,
                height=height,
                local_path=local_path,
            )
            db.add(asset)
            db.flush()
        else:
            asset.last_seen_at = utcnow()
            asset.dhash = asset.dhash or dhash
            asset.width = asset.width or width
            asset.height = asset.height or height

        source.last_remote_url = ref.url
        source.revision_token = ref.revision_token
        source.etag = response.headers.get("ETag")
        source.last_modified = response.headers.get("Last-Modified")
        try:
            source.content_length = int(response.headers.get("Content-Length") or len(data))
        except ValueError:
            source.content_length = len(data)
        source.media_asset_id = asset.media_asset_id
        source.last_checked_at = utcnow()
        return ArchivedMedia(asset, source, ref.url, base, ref.revision_token, source.etag, source.last_modified)
