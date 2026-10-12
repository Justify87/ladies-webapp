from __future__ import annotations


class SearchRepository:
    def __init__(self, store):
        self._store = store

    def create_search_run(self, **kwargs):
        return self._store.create_search_run(**kwargs)

    def finish_search_run(self, search_run_id, profiles, meta):
        return self._store.finish_search_run(search_run_id, profiles, meta)

    def recent_searches(self, limit: int = 8):
        return self._store.recent_searches(limit)
