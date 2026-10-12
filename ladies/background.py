from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from collections.abc import Callable
from typing import Any

LOGGER = logging.getLogger("ladies_explorer.background")


class BackgroundTasks:
    """Small bounded executor for supplementary local work.

    Tasks may be deduplicated by key. Failures are logged and never escape back
    into the request that scheduled them.
    """

    def __init__(self, max_workers: int = 2):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ladies-bg")
        self._lock = threading.Lock()
        self._inflight: dict[str, Future[Any]] = {}
        self._closed = False

    def submit(self, key: str, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Future[Any] | None:
        with self._lock:
            if self._closed:
                return None
            existing = self._inflight.get(key)
            if existing is not None and not existing.done():
                return existing
            future = self._executor.submit(function, *args, **kwargs)
            self._inflight[key] = future

        def finished(done: Future[Any]) -> None:
            try:
                done.result()
            except Exception:
                LOGGER.exception("Background task failed key=%s", key)
            finally:
                with self._lock:
                    if self._inflight.get(key) is done:
                        self._inflight.pop(key, None)

        future.add_done_callback(finished)
        return future

    def shutdown(self, *, wait: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=True)
