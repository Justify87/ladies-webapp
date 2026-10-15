from __future__ import annotations

import copy
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

LOGGER = logging.getLogger("ladies_explorer.saved_searches")
MAX_SAVED_SEARCHES = 25
BACKGROUND_REFRESH_SECONDS = 15 * 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _unique_result_keys(results: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    keys: list[str] = []
    for row in results:
        key = _result_key(row)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def baseline_from_results(results: list[dict[str, Any]], completed_at: str) -> dict[str, Any]:
    return {"completed_at": completed_at, "keys": _unique_result_keys(results)}


def compute_result_delta(
    item: dict[str, Any],
    results: list[dict[str, Any]],
    completed_at: str,
) -> dict[str, Any]:
    current = baseline_from_results(results, completed_at)
    previous = item.get("result_baseline")
    if not isinstance(previous, dict) or not isinstance(previous.get("keys"), list):
        return {
            "initial": True,
            "current_result_count": len(current["keys"]),
            "new_results_count": 0,
            "removed_results_count": 0,
            "new_result_keys": [],
        }

    previous_keys = {str(value) for value in previous.get("keys", []) if str(value)}
    seen_source = item.get("seen_result_keys")
    seen_keys = {
        str(value)
        for value in (seen_source if isinstance(seen_source, list) and seen_source else previous.get("keys", []))
        if str(value)
    }
    current_keys = set(current["keys"])
    new_keys = [key for key in current["keys"] if key not in seen_keys]
    removed = [key for key in previous_keys if key not in current_keys]
    return {
        "initial": False,
        "current_result_count": len(current["keys"]),
        "new_results_count": len(new_keys),
        "removed_results_count": len(removed),
        "new_result_keys": new_keys,
    }


def filter_saved_search_results(
    results: list[dict[str, Any]],
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply the same local visibility semantics used by the Explorer result selectors."""
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
    """Owns saved-search state, execution and race-safe mutation."""

    def __init__(self, settings_repository, search_service, *, business_classifier=None):
        self.settings = settings_repository
        self.search = search_service
        self.business_classifier = business_classifier
        self._lock = threading.RLock()
        self._run_locks: dict[str, threading.Lock] = {}

    def _items(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self.settings.get_saved_searches())

    def _store(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return copy.deepcopy(self.settings.set_saved_searches(items[:MAX_SAVED_SEARCHES]))

    def _classify_business_results(
        self,
        results: list[dict[str, Any]],
        filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        local = filters.get("local") if isinstance(filters.get("local"), dict) else {}
        if local.get("hideBusinessProfiles") is False or self.business_classifier is None:
            return results

        enriched: list[dict[str, Any]] = []
        for row in results:
            current = dict(row)
            if not bool(current.get("business_profile_known")):
                advert_id = _result_key(current)
                if advert_id:
                    classification = self.business_classifier(advert_id)
                    if isinstance(classification, dict):
                        current.update(classification)
            enriched.append(current)
        return enriched

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._items()

    def create(self, *, name: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("Name fehlt")
        if not isinstance(filters, dict):
            raise ValueError("Filter fehlen")
        now = _now_iso()
        item = {
            "id": str(uuid.uuid4()),
            "name": clean_name,
            "filters": copy.deepcopy(filters),
            "created_at": now,
            "updated_at": now,
            "last_run_at": None,
            "result_baseline": None,
            "seen_result_keys": [],
            "last_delta": None,
            "unread_new_results": False,
            "new_results_at": None,
        }
        with self._lock:
            return self._store([item, *self._items()])

    def update(self, saved_id: str, *, name: str, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("Name fehlt")
        if not isinstance(filters, dict):
            raise ValueError("Filter fehlen")
        with self._lock:
            items = self._items()
            current = next((item for item in items if str(item.get("id")) == str(saved_id)), None)
            if current is None:
                raise KeyError(saved_id)
            changed = current.get("filters") != filters
            now = _now_iso()
            updated = []
            for item in items:
                if str(item.get("id")) != str(saved_id):
                    updated.append(item)
                    continue
                next_item = {
                    **item,
                    "name": clean_name,
                    "filters": copy.deepcopy(filters),
                    "updated_at": now,
                }
                if changed:
                    next_item.update({
                        "last_run_at": None,
                        "result_baseline": None,
                        "seen_result_keys": [],
                        "last_delta": None,
                        "unread_new_results": False,
                        "new_results_at": None,
                    })
                updated.append(next_item)
            return self._store(updated), changed

    def delete(self, saved_id: str) -> list[dict[str, Any]]:
        with self._lock:
            items = self._items()
            next_items = [item for item in items if str(item.get("id")) != str(saved_id)]
            if len(next_items) == len(items):
                raise KeyError(saved_id)
            return self._store(next_items)

    def acknowledge_all(self) -> list[dict[str, Any]]:
        with self._lock:
            items = self._items()
            if not any(bool(item.get("unread_new_results")) for item in items):
                return items
            return self._store([
                {**item, "unread_new_results": False} if item.get("unread_new_results") else item
                for item in items
            ])

    def _run_lock(self, saved_id: str) -> threading.Lock:
        with self._lock:
            return self._run_locks.setdefault(str(saved_id), threading.Lock())

    def run(self, saved_id: str, *, mark_seen: bool) -> dict[str, Any]:
        with self._run_lock(saved_id):
            with self._lock:
                snapshot = next(
                    (item for item in self._items() if str(item.get("id")) == str(saved_id)),
                    None,
                )
                if snapshot is None:
                    raise KeyError(saved_id)
                revision = snapshot.get("updated_at")
                filters = copy.deepcopy(snapshot.get("filters") or {})

            data = self.search.execute_transient(search_payload_from_filters(filters))
            canonical_results = self._classify_business_results(data.get("results") or [], filters)
            visible_results = filter_saved_search_results(canonical_results, filters)
            completed_at = _now_iso()
            baseline = baseline_from_results(visible_results, completed_at)
            delta = compute_result_delta(snapshot, visible_results, completed_at)

            with self._lock:
                items = self._items()
                current = next((item for item in items if str(item.get("id")) == str(saved_id)), None)
                if current is None or current.get("updated_at") != revision:
                    return {"items": items, "delta": None, "stale": True}

                previous = current.get("result_baseline") or {}
                previous_keys = previous.get("keys") if isinstance(previous.get("keys"), list) else []
                seen = list(dict.fromkeys([
                    *[str(value) for value in current.get("seen_result_keys") or []],
                    *[str(value) for value in previous_keys],
                    *baseline["keys"],
                ]))
                next_items = []
                for item in items:
                    if str(item.get("id")) != str(saved_id):
                        next_items.append(item)
                        continue
                    has_new = int(delta.get("new_results_count") or 0) > 0
                    next_items.append({
                        **item,
                        "last_run_at": completed_at,
                        "result_baseline": baseline,
                        "seen_result_keys": seen,
                        "last_delta": delta,
                        "unread_new_results": False if mark_seen else bool(item.get("unread_new_results") or has_new),
                        "new_results_at": completed_at if has_new else item.get("new_results_at"),
                    })
                stored = self._store(next_items)
                return {"items": stored, "delta": delta, "stale": False}

    def refresh_due(self) -> None:
        now = datetime.now(timezone.utc)
        for item in self.list():
            last = _parse_time(item.get("last_run_at"))
            if last is not None and (now - last).total_seconds() < BACKGROUND_REFRESH_SECONDS:
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
