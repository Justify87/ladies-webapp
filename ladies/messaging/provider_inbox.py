from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

DURABLE_EVENT_TYPES = {"message", "history_batch"}


class DurableProviderInbox:
    """Tiny disk-backed at-least-once queue for provider events.

    Catch-up events are persisted before the canonical messaging service sees
    them. The file is removed only after the service returns successfully. A
    crash or transient database failure therefore turns into a replay on the
    next drain instead of a silently lost WhatsApp message. Canonical provider
    IDs make replay idempotent in the SQL store.
    """

    def __init__(self, root: str | Path, handler: Callable[[dict[str, Any]], None]):
        self.root = Path(root)
        self.handler = handler
        self._lock = threading.RLock()

    def _write(self, event: dict[str, Any]) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        name = f"{time.time_ns():020d}-{uuid.uuid4().hex}.json"
        target = self.root / name
        temporary = self.root / f".{name}.{os.getpid()}.tmp"
        temporary.write_text(
            json.dumps(event, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, target)
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

    def pending_count(self) -> int:
        with self._lock:
            return len(self._files())

    def deliver(self, event: dict[str, Any]) -> None:
        with self._lock:
            pending = self._write(event)
            try:
                self.handler(event)
            except Exception:
                # Leave the durable copy in place for the next drain/restart.
                raise
            else:
                try:
                    pending.unlink()
                except FileNotFoundError:
                    pass

    def drain(self) -> int:
        delivered = 0
        with self._lock:
            for path in self._files():
                event = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(event, dict):
                    raise ValueError(f"invalid pending provider event: {path.name}")
                self.handler(event)
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                delivered += 1
        return delivered
