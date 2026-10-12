from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ladies_v9.database import Database


class ApplicationContextTests(unittest.TestCase):
    def test_schema_initialization_runs_once_per_database_instance(self):
        with tempfile.TemporaryDirectory() as td:
            database = Database(Path(td) / "app.sqlite3")
            original = database._create_schema
            calls = []

            def counted():
                calls.append(True)
                return original()

            with patch.object(database, "_create_schema", side_effect=counted):
                database.create_schema()
                database.create_schema()
                database.create_schema()

            self.assertEqual(len(calls), 1)
            self.assertEqual(database.schema_version(), 1)

    def test_same_path_handles_share_engine_and_schema_state(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "app.sqlite3"
            core = Database(path)
            messaging = Database(path)
            pricing = Database(path)

            self.assertIs(core.engine, messaging.engine)
            self.assertIs(core.engine, pricing.engine)
            self.assertIs(core.Session, messaging.Session)
            self.assertIs(core.Session, pricing.Session)

            core.create_schema()
            with patch.object(messaging, "_create_schema") as duplicate_schema_init:
                messaging.create_schema()
            duplicate_schema_init.assert_not_called()


if __name__ == "__main__":
    unittest.main()
