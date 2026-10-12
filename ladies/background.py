from __future__ import annotations

import atexit
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Hashable

LOGGER = logging.getLogger("ladies_explorer.background")


class BackgroundTasks:
    """Small bounded executor for best-effort in-process application work.

    Tasks can be keyed so repeated HTTP actions do not spawn duplicate work for
    the same entity. This deliberately stays in-process: the local single-user
    application does not need Redis, Celery or another service.
    """

    def __init__(self, *, max_workers: int = 2):
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="ladies-bg",
        )
        self._lock = threading.RLock()
        self._pending: set[Hashable] = set()
        self._closed = False
        atexit.register(self.shutdown)

    def submit(
        self,
        key: Hashable,
        fn: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Future | None:
        with self._lock:
            if self._closed:
                return None
            if key in self._pending:
                return None
            self._pending.add(key)

        def run():
            try:
                return fn(*args, **kwargs)
            except Exception:
                LOGGER.exception("Background task failed key=%r", key)
                return None
            finally:
                with self._lock:
                    self._pending.discard(key)

        try:
            return self._executor.submit(run)
        except Exception:
            with self._lock:
                self._pending.discard(key)
            raise

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)
