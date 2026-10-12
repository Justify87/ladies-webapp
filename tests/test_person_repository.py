from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ladies_v9.core.repositories.persons import PersonRepository
from ladies_v9.core.repositories.settings import SettingsRepository
from ladies_v9.database import Database
from ladies_v9.media import ArchiveManager
from ladies_v9.models import Listing, Person, PersonUserState


class DummyReadStore:
    def __getattr__(self, name):
        raise AssertionError(f"legacy read store unexpectedly used: {name}")


class PersonRepositoryTests(unittest.TestCase):
    def make_repository(self, root: Path):
        database = Database(root / "app.sqlite3")
        database.create_schema()
        archive = ArchiveManager(root / "data")
        settings = SettingsRepository(database)
        return database, PersonRepository(database, archive, settings, DummyReadStore())

    @staticmethod
    def seed_person(database: Database):
        with database.session() as db:
            person = Person(display_name="Anna")
            db.add(person)
            db.flush()
            db.add(PersonUserState(person_id=person.person_id))
            listing = Listing(
                person_id=person.person_id,
                source="LADIES",
                external_listing_id="123",
                current_url="https://www.ladies.de/sex-anzeigen/123",
                current_status="ACTIVE",
            )
            db.add(listing)
            db.flush()
            return int(person.person_id), int(listing.listing_id)

    def test_state_status_price_and_encounter_do_not_use_legacy_store(self):
        with tempfile.TemporaryDirectory() as td:
            database, repository = self.make_repository(Path(td))
            person_id, _ = self.seed_person(database)

            target = repository.listing_target("123")
            self.assertEqual(target["person_id"], person_id)

            state = repository.set_user_state(
                person_id,
                {"preference": "LIKE", "favorite": True, "private_note": "Merken"},
            )
            self.assertEqual(state["preference"], "LIKE")
            self.assertTrue(state["favorite"])

            status = repository.update_listing_status("123", status="UNAVAILABLE", http_status=404)
            self.assertEqual(status["previous"], "ACTIVE")
            self.assertEqual(status["status"], "UNAVAILABLE")

            prices = repository.add_price_list(
                person_id,
                {"items": [{"label": "30 Minuten", "duration_minutes": 30, "amount_eur": 100}]},
            )
            self.assertEqual(prices["items"][0]["amount_cents"], 10000)

            encounter = repository.add_encounter(
                person_id,
                {
                    "duration_minutes": 60,
                    "total_price_eur": 160,
                    "services": [{"service_name": "Küssen", "performed": True}],
                },
            )
            self.assertEqual(encounter["total_price_eur"], 160)
            self.assertEqual(encounter["services"][0]["service_name"], "Küssen")

            updated = repository.update_encounter(
                person_id,
                encounter["encounter_id"],
                {"total_price_eur": 170, "services": [{"service_name": "Massage"}]},
            )
            self.assertEqual(updated["total_price_eur"], 170)
            self.assertEqual([row["service_name"] for row in updated["services"]], ["Massage"])

            targets = repository.status_targets()
            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0]["advert_id"], "123")

    def test_invalid_state_and_status_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            database, repository = self.make_repository(Path(td))
            person_id, _ = self.seed_person(database)
            with self.assertRaises(ValueError):
                repository.set_user_state(person_id, {"preference": "MAYBE"})
            with self.assertRaises(ValueError):
                repository.update_listing_status("123", status="BROKEN")


if __name__ == "__main__":
    unittest.main()
