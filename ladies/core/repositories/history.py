from __future__ import annotations


class HistoryRepository:
    def __init__(self, store):
        self._store = store

    def dashboard(self):
        return self._store.dashboard()

    def relistings(self, limit: int = 100):
        return self._store.history_relistings(limit)

    def changes(self, limit: int = 100):
        return self._store.history_changes(limit)

    def snapshot_detail(self, snapshot_id: int):
        return self._store.snapshot_detail(snapshot_id)
