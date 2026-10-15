from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

LOGGER = logging.getLogger("ladies_explorer.saved_searches")
MAX_SAVED_SEARCHES = 25
BACKGROUND_REFRESH_SECONDS = 15 * 60
BACKGROUND_RETRY_SECONDS = 5 * 60


def _parse_time(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _result_key(row: dict[str, Any]) -> str:
    return str(row.get("advert_id") or row.get("external_listing_id") or "").strip()


def filter_saved_search_results(
    results: list[dict[str, Any]],
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply the same local visibility semantics as the active Explorer selectors."""
    local = filters.get("local") if isinstance(filters.get("local"), dict) else {}
    mode = str(local.get("imageMode") or "ALL").upper()
    out: list[dict[str, Any]] = []
    for row in results:
        if local.get("videoOnly") and not bool(row.get("has_video")):
            continue
        if local.get("hideDislikes") is not False and str(row.get("preference") or "NONE") == "DISLIKE":
            continue
        if local.get("hideLikes") is True and str(row.get("preference") or "NONE") == "LIKE":
            continue
        # Unknown classifications behave exactly like the active result selector:
        # only a positively known business profile is hidden.
        if local.get("hideBusinessProfiles") is not False and bool(row.get("is_business_profile")):
            continue
        if local.get("photosOnly") and not str(row.get("thumbnail_url") or "").strip():
            continue

        has_ai = bool(row.get("has_ai_content"))
        marker_source = row.get("markers")
        markers = (
            json.dumps(marker_source, ensure_ascii=False, sort_keys=True)
            if isinstance(marker_source, (dict, list))
            else str(marker_source or "")
        ).lower()
        if mode == "AI" and not has_ai:
            continue
        if mode == "NATURAL" and has_ai:
            continue
        if mode == "STUDIO" and "studio" not in markers:
            continue
        if mode == "FACEAPP" and "face" not in markers:
            continue
        out.append(row)
    return out


def search_payload_from_filters(filters: dict[str, Any]) -> dict[str, Any]:
    return {
        "city": filters.get("city"),
        "radius": filters.get("radius"),
        "category": filters.get("category"),
        "age_min": filters.get("ageMin"),
        "age_max": filters.get("ageMax"),
        "height_min": filters.get("heightMin"),
        "height_max": filters.get("heightMax"),
        "selected": filters.get("selected") or {},
    }


class SavedSearchService:
    """Owns saved-search definitions, read-only execution and result history."""

    def __init__(self, repository, search_service):
        self.repository = repository
        self.search = search_service
        self._lock = threading.RLock()
        self._run_locks: dict[str, threading.Lock] = {}

    def list(self) -> list[dict[str, Any]]:
        return self.repository.list()

    def create(self, *, name: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("Name fehlt")
        if not isinstance(filters, dict):
            raise ValueError("Filter fehlen")
        with self._lock:
            if len(self.repository.list()) >= MAX_SAVED_SEARCHES:
                raise ValueError(f"Maximal {MAX_SAVED_SEARCHES} gespeicherte Suchen sind möglich")
            return self.repository.create(str(uuid.uuid4()), name=clean_name, filters=filters)

    def update(self, saved_id: str, *, name: str, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("Name fehlt")
        if not isinstance(filters, dict):
            raise ValueError("Filter fehlen")
        return self.repository.update_definition(saved_id, name=clean_name, filters=filters)

    def delete(self, saved_id: str) -> list[dict[str, Any]]:
        return self.repository.delete(saved_id)

    def acknowledge_all(self) -> list[dict[str, Any]]:
        return self.repository.acknowledge_all()

    def results(self, saved_id: str) -> list[dict[str, Any]]:
        return self.repository.results(saved_id)

    def materialize_result(self, saved_id: str, result_key: str) -> dict[str, Any]:
        payload = self.repository.result(saved_id, result_key)
        if payload is None:
            raise KeyError(result_key)
        return self.search.materialize_probe_result(payload)

    def _run_lock(self, saved_id: str) -> threading.Lock:
        with self._lock:
            return self._run_locks.setdefault(str(saved_id), threading.Lock())

    def run(self, saved_id: str, *, mark_seen: bool) -> dict[str, Any]:
        with self._run_lock(saved_id):
            snapshot = self.repository.get(saved_id)
            if snapshot is None:
                raise KeyError(saved_id)
            definition_version = int(snapshot.get("definition_version") or 1)
            filters = snapshot.get("filters") or {}

            try:
                data = self.search.execute_probe(search_payload_from_filters(filters))
                results = data.get("results") or []
                visible = filter_saved_search_results(results, filters)
                visible_keys = {_result_key(row) for row in visible if _result_key(row)}
                outcome = self.repository.record_success(
                    saved_id,
                    definition_version=definition_version,
                    results=results,
                    visible_keys=visible_keys,
                    mark_seen=mark_seen,
                )
            except Exception as exc:
                self.repository.record_failure(
                    saved_id,
                    definition_version=definition_version,
                    error=str(exc),
                )
                raise

            return {
                "items": self.repository.list(),
                "delta": outcome.get("delta"),
                "stale": bool(outcome.get("stale")),
            }

    def refresh_due(self) -> None:
        now = datetime.now(timezone.utc)
        for item in self.repository.list():
            failed = bool(item.get("last_error"))
            reference = _parse_time(item.get("last_attempt_at") if failed else item.get("last_run_at"))
            minimum_age = BACKGROUND_RETRY_SECONDS if failed else BACKGROUND_REFRESH_SECONDS
            if reference is not None and (now - reference).total_seconds() < minimum_age:
                continue
            try:
                self.run(str(item.get("id") or ""), mark_seen=False)
            except KeyError:
                continue
            except Exception:
                LOGGER.exception("Saved search refresh failed id=%s", item.get("id"))


class SavedSearchScheduler:
    """Periodic server-side trigger; actual work runs through the bounded background executor."""

    def __init__(self, service: SavedSearchService, background, *, check_seconds: float = 60.0):
        self.service = service
        self.background = background
        self.check_seconds = max(1.0, float(check_seconds))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="ladies-saved-search-scheduler", daemon=True)
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()

    def _submit(self) -> None:
        self.background.submit("saved-searches:refresh", self.service.refresh_due)

    def _loop(self) -> None:
        self._submit()
        while not self._stop.wait(self.check_seconds):
            self._submit()

    def shutdown(self) -> None:
        self._stop.set()
        if self._started and self._thread.is_alive():
            self._thread.join(timeout=2.0)
