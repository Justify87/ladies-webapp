from __future__ import annotations

from pathlib import Path
from typing import Any


class PricingMediaLocator:
    """Resolve locally archived chat media without depending on HTTP URLs."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).resolve()
        self.whatsapp_media_dir = (self.data_dir / "whatsapp" / "media").resolve()

    @staticmethod
    def _safe_name(value: Any) -> str | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        name = Path(raw).name
        return name if name and name not in {".", ".."} else None

    def locate(self, media: dict[str, Any] | None) -> Path | None:
        if not isinstance(media, dict):
            return None

        archive_name = self._safe_name(media.get("archive_name"))
        if archive_name:
            candidate = (self.whatsapp_media_dir / archive_name).resolve()
            try:
                candidate.relative_to(self.whatsapp_media_dir)
            except ValueError:
                return None
            if candidate.is_file():
                return candidate

        # Compatibility with pre-V9.6 rows. The URL is treated only as a
        # relative legacy storage hint and is never fetched over HTTP.
        local_url = str(media.get("local_url") or "")
        if local_url.startswith("/archive/"):
            candidate = (self.data_dir / local_url[len("/archive/"):]).resolve()
            try:
                candidate.relative_to(self.data_dir)
            except ValueError:
                return None
            if candidate.is_file():
                return candidate
        return None
