from __future__ import annotations


class ArchiveRepository:
    def __init__(self, store):
        self._store = store

    def storage_info(self):
        return self._store.storage_info()

    def items(self, *, kind=None, limit: int = 80):
        return self._store.archive_items(kind=kind, limit=limit)

    def clear_visual_analyses(self):
        return self._store.clear_visual_analyses()

    def save_visual_analysis(self, media_asset_id: int, payload):
        return self._store.save_visual_analysis(media_asset_id, payload)
