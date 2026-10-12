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


if __name__ == "__main__":
    unittest.main()
