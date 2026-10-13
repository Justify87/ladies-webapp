from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from collections.abc import Callable
from typing import Any

LOGGER = logging.getLogger("ladies_explorer.background")


class BackgroundTasks:
    """Small bounded executor for supplementary local work.

    Tasks may be deduplicated by key. The pending capacity includes running and
    queued work so callers cannot grow an unbounded ThreadPoolExecutor backlog.
    Failures are logged and never escape back into the request that scheduled
    them.
    """

    def __init__(self, max_workers: int = 2, max_pending: int = 16):
        if max_workers < 1:
            raise ValueError("max_workers muss mindestens 1 sein")
        if max_pending < max_workers:
            raise ValueError("max_pending muss mindestens max_workers entsprechen")
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ladies-bg")
        self._capacity = threading.BoundedSemaphore(max_pending)
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
            if not self._capacity.acquire(blocking=False):
                LOGGER.warning("Background task rejected because backlog is full key=%s", key)
                return None
            try:
                future = self._executor.submit(function, *args, **kwargs)
            except Exception:
                self._capacity.release()
                raise
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
                self._capacity.release()

        future.add_done_callback(finished)
        return future

    def shutdown(self, *, wait: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=True)