from __future__ import annotations


class SettingsRepository:
    def __init__(self, store):
        self._store = store

    def get_all(self):
        return self._store.settings()

    def set(self, key, value):
        return self._store.set_setting(key, value)
