from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from ladies_v9.database import CURRENT_SCHEMA_VERSION, Database


PERSON_COLUMNS_WITHOUT_DISPLAY_NAME = """
    person_id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_seen_at DATETIME NOT NULL,
    last_seen_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
"""


class DatabaseMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "ladies.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_database_is_stamped_without_migration_backup(self):
        database = Database(self.db_path)
        database.create_schema()

        self.assertEqual(database.schema_version(), CURRENT_SCHEMA_VERSION)
        backup_dir = self.root / "migration-backups"
        self.assertFalse(backup_dir.exists())

    def test_preversioned_database_is_upgraded_and_existing_rows_survive(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(f"CREATE TABLE persons ({PERSON_COLUMNS_WITHOUT_DISPLAY_NAME})")
            connection.execute(
                """
                INSERT INTO persons(first_seen_at, last_seen_at, created_at, updated_at)
                VALUES ('2026-01-01', '2026-01-02', '2026-01-01', '2026-01-02')
                """
            )
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)

        database = Database(self.db_path)
        database.create_schema()

        self.assertEqual(database.schema_version(), CURRENT_SCHEMA_VERSION)
        with sqlite3.connect(self.db_path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(persons)")}
            row = connection.execute(
                "SELECT person_id, display_name, first_seen_at FROM persons"
            ).fetchone()
        self.assertIn("display_name", columns)
        self.assertEqual(row[0], 1)
        self.assertIsNone(row[1])
        self.assertEqual(row[2], "2026-01-01")

        backups = list((self.root / "migration-backups").glob("*.sqlite3"))
        self.assertEqual(len(backups), 1)
        with sqlite3.connect(backups[0]) as backup:
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM persons").fetchone()[0], 1)
            old_columns = {row[1] for row in backup.execute("PRAGMA table_info(persons)")}
        self.assertNotIn("display_name", old_columns)

    def test_unsafe_legacy_shape_stops_instead_of_guessing_data(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE persons (
                    person_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    display_name VARCHAR(160),
                    first_seen_at DATETIME NOT NULL,
                    last_seen_at DATETIME NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO persons(display_name, first_seen_at, last_seen_at, created_at)
                VALUES ('Altbestand', '2026-01-01', '2026-01-02', '2026-01-01')
                """
            )

        database = Database(self.db_path)
        with self.assertRaisesRegex(RuntimeError, "NOT NULL ohne Server-Default"):
            database.create_schema()

        self.assertEqual(database.schema_version(), 0)
        backups = list((self.root / "migration-backups").glob("*.sqlite3"))
        self.assertEqual(len(backups), 1)
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM persons").fetchone()[0], 1)

    def test_newer_database_is_never_downgraded(self):
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("CREATE TABLE marker (id INTEGER PRIMARY KEY)")
            connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION + 1}")

        database = Database(self.db_path)
        with self.assertRaisesRegex(RuntimeError, "ist neuer als diese Anwendung"):
            database.create_schema()
        self.assertEqual(database.schema_version(), CURRENT_SCHEMA_VERSION + 1)


if __name__ == "__main__":
    unittest.main()
