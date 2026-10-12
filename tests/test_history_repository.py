from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ladies_v9.core.repositories.history import HistoryRepository
from ladies_v9.database import Database
from ladies_v9.models import Listing, Person, PersonUserState


class HistoryRepositoryTests(unittest.TestCase):
    def make_repository(self, root: Path):
        database = Database(root / "app.sqlite3")
        database.create_schema()
        return database, HistoryRepository(database)

    def test_dashboard_and_relisting_counts_use_shared_database(self):
        with tempfile.TemporaryDirectory() as td:
            database, repository = self.make_repository(Path(td))
            with database.session() as db:
                person = Person(display_name="Anna")
                db.add(person)
                db.flush()
                db.add(PersonUserState(person_id=person.person_id, preference="LIKE", favorite=True))
                db.add_all([
                    Listing(
                        person_id=person.person_id,
                        source="LADIES",
                        external_listing_id="100",
                        current_status="ACTIVE",
                    ),
                    Listing(
                        person_id=person.person_id,
                        source="LADIES",
                        external_listing_id="101",
                        current_status="ACTIVE",
                    ),
                ])

            dashboard = repository.dashboard()
            self.assertEqual(dashboard["counts"]["persons"], 1)
            self.assertEqual(dashboard["counts"]["listings"], 2)
            self.assertEqual(dashboard["counts"]["people_with_relistings"], 1)
            self.assertEqual(dashboard["counts"]["likes"], 1)
            self.assertEqual(dashboard["counts"]["favorites"], 1)

            relistings = repository.relistings()
            self.assertEqual(len(relistings), 1)
            self.assertEqual(relistings[0]["listing_count"], 2)
            self.assertEqual(relistings[0]["name"], "Anna")

    def test_unknown_snapshot_is_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            _, repository = self.make_repository(Path(td))
            with self.assertRaises(KeyError):
                repository.snapshot_detail(999)


if __name__ == "__main__":
    unittest.main()
