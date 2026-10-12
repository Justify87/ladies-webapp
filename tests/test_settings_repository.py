from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ladies_v9.core.repositories.settings import DEFAULT_SETTINGS, SettingsRepository
from ladies_v9.database import Database


class SettingsRepositoryTests(unittest.TestCase):
    def test_defaults_and_updates_persist_through_shared_database(self):
        with tempfile.TemporaryDirectory() as td:
            database = Database(Path(td) / "app.sqlite3")
            database.create_schema()
            repository = SettingsRepository(database)

            settings = repository.get_all()
            for key, value in DEFAULT_SETTINGS.items():
                self.assertEqual(settings[key], value)

            updated = repository.set("reduced_motion", True)
            self.assertTrue(updated["reduced_motion"])

            second = SettingsRepository(database)
            self.assertTrue(second.get_all()["reduced_motion"])

    def test_unknown_setting_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            database = Database(Path(td) / "app.sqlite3")
            database.create_schema()
            repository = SettingsRepository(database)
            with self.assertRaises(ValueError):
                repository.set("does_not_exist", True)


if __name__ == "__main__":
    unittest.main()
