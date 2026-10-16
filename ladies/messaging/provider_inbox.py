from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

LOGGER = logging.getLogger("ladies_explorer.messaging.provider_inbox")
DURABLE_EVENT_TYPES = {"message", "history_batch"}


class DurableProviderInbox:
    """Small disk-backed at-least-once queue for reconnect catch-up events."""

    def __init__(self, root: str | Path, handler: Callable[[dict[str, Any]], None]):
        self.root = Path(root)
        self.quarantine_dir = self.root / "quarantine"
        self.handler = handler
        self._lock = threading.RLock()

    @staticmethod
    def _sync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            fd = os.open(path, flags)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def _write(self, event: dict[str, Any]) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        name = f"{time.time_ns():020d}-{uuid.uuid4().hex}.json"
        target = self.root / name
        temporary = self.root / f".{name}.{os.getpid()}.tmp"
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, target)
        self._sync_directory(self.root)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
        return target

    def _files(self) -> list[Path]:
        try:
            return sorted(path for path in self.root.glob("*.json") if path.is_file())
        except OSError:
            return []

    def _temporary_files(self) -> list[Path]:
        try:
            return sorted(path for path in self.root.glob(".*.tmp") if path.is_file())
        except OSError:
            return []

    def _quarantine(self, path: Path, reason: str) -> Path:
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        target = self.quarantine_dir / f"{time.time_ns():020d}-{reason}-{path.name.lstrip('.')}"
        os.replace(path, target)
        self._sync_directory(self.root)
        self._sync_directory(self.quarantine_dir)
        LOGGER.error("Quarantined WhatsApp ingress file name=%s reason=%s", target.name, reason)
        return target

    @staticmethod
    def _read_event(path: Path) -> dict[str, Any]:
        event = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(event, dict):
            raise ValueError("pending provider event is not an object")
        return event

    def _recover_temporaries(self) -> tuple[int, int]:
        recovered = 0
        quarantined = 0
        for path in self._temporary_files():
            try:
                self._read_event(path)
            except Exception:
                self._quarantine(path, "corrupt-temp")
                quarantined += 1
                continue
            target = self.root / f"{time.time_ns():020d}-{uuid.uuid4().hex}.json"
            os.replace(path, target)
            self._sync_directory(self.root)
            recovered += 1
        return recovered, quarantined

    def pending_count(self) -> int:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            self._recover_temporaries()
            return len(self._files())

    def quarantined_count(self) -> int:
        with self._lock:
            try:
                return sum(1 for path in self.quarantine_dir.iterdir() if path.is_file())
            except (FileNotFoundError, OSError):
                return 0

    def deliver(self, event: dict[str, Any]) -> None:
        with self._lock:
            pending = self._write(event)
            try:
                self.handler(event)
            except Exception:
                raise
            else:
                try:
                    pending.unlink()
                    self._sync_directory(self.root)
                except FileNotFoundError:
                    pass

    def drain(self) -> tuple[int, int]:
        delivered = 0
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            _, quarantined = self._recover_temporaries()
            for path in self._files():
                try:
                    event = self._read_event(path)
                except Exception:
                    self._quarantine(path, "corrupt")
                    quarantined += 1
                    continue
                self.handler(event)
                try:
                    path.unlink()
                    self._sync_directory(self.root)
                except FileNotFoundError:
                    pass
                delivered += 1
        return delivered, quarantined

    def abandon(self, reason: str = "reset") -> int:
        """Quarantine session-bound pending events before credentials are discarded."""
        moved = 0
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            for path in [*self._files(), *self._temporary_files()]:
                try:
                    self._quarantine(path, reason)
                except FileNotFoundError:
                    continue
                moved += 1
        return moved
