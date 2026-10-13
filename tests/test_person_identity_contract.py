from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ladies_v9.core.repositories import RepositoryBundle
from ladies_v9.database import Database
from ladies_v9.domain import ProfileData
from ladies_v9.media import ArchiveManager


class PersonIdentityContractTests(unittest.TestCase):
    def make_repository(self, root: Path):
        database = Database(root / "app.sqlite3")
        database.create_schema()
        archive = ArchiveManager(root / "data")
        return RepositoryBundle.from_dependencies(database, archive).persons

    @staticmethod
    def profile(advert_id: str, *, name="Anna", city="Leipzig", phone="0176 12345678"):
        return ProfileData(
            advert_id=advert_id,
            source_profile_id=f"profile-{advert_id}",
            canonical_id=f"canonical-{advert_id}",
            name=name,
            city=city,
            postal_code="04109",
            hidden_location=None,
            category="huren-hostessen",
            source_url=f"https://www.ladies.de/sex-anzeigen/{advert_id}",
            description="Beschreibung",
            phone_display=phone,
            traits={"Haarfarbe": ["braun"]},
            services={"Service": ["Küssen"]},
            opening_times={"Mo": "10-20"},
            media=[],
        )

    def test_same_profile_is_idempotent_and_change_creates_new_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            repository = self.make_repository(Path(td))
            first = repository.ingest_profile(
                external_listing_id="100",
                profile=self.profile("100"),
                raw_html="<html>one</html>",
                http_session=None,
            )
            second = repository.ingest_profile(
                external_listing_id="100",
                profile=self.profile("100"),
                raw_html="<html>two</html>",
                http_session=None,
            )
            self.assertEqual(first["identity"]["person_id"], second["identity"]["person_id"])
            self.assertEqual(first["profile"]["snapshot_id"], second["profile"]["snapshot_id"])

            changed = repository.ingest_profile(
                external_listing_id="100",
                profile=self.profile("100", city="Berlin"),
                raw_html="<html>three</html>",
                http_session=None,
            )
            self.assertNotEqual(changed["profile"]["snapshot_id"], first["profile"]["snapshot_id"])
            detail = repository.person_detail(first["identity"]["person_id"])
            self.assertTrue(any(row["field_key"] == "city" for row in detail["timeline"]))

    def test_same_phone_can_merge_new_listing_into_existing_person(self):
        with tempfile.TemporaryDirectory() as td:
            repository = self.make_repository(Path(td))
            first = repository.ingest_profile(
                external_listing_id="100",
                profile=self.profile("100"),
                raw_html="a",
                http_session=None,
            )
            second = repository.ingest_profile(
                external_listing_id="200",
                profile=self.profile("200", name="Anna Neu"),
                raw_html="b",
                http_session=None,
            )
            self.assertEqual(first["identity"]["person_id"], second["identity"]["person_id"])
            detail = repository.person_detail(first["identity"]["person_id"])
            self.assertEqual(detail["person"]["listing_count"], 2)

    def test_manual_split_blocks_automatic_remerge(self):
        with tempfile.TemporaryDirectory() as td:
            repository = self.make_repository(Path(td))
            first = repository.ingest_profile(
                external_listing_id="100",
                profile=self.profile("100"),
                raw_html="a",
                http_session=None,
            )
            repository.ingest_profile(
                external_listing_id="200",
                profile=self.profile("200"),
                raw_html="b",
                http_session=None,
            )
            listing = repository.listing_target("200")
            split = repository.split_listing(listing["listing_id"])
            self.assertNotEqual(split["new_person_id"], first["identity"]["person_id"])

            refreshed = repository.ingest_profile(
                external_listing_id="200",
                profile=self.profile("200"),
                raw_html="c",
                http_session=None,
            )
            self.assertEqual(refreshed["identity"]["person_id"], split["new_person_id"])
            self.assertEqual(refreshed["identity"]["match_decision"], "MANUAL_SPLIT")


if __name__ == "__main__":
    unittest.main()
