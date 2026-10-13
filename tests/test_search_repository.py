from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import func, select

from ladies_v9.core.repositories.search import SearchRepository
from ladies_v9.database import Database
from ladies_v9.domain import SearchProfile
from ladies_v9.models import Listing, ListingObservation, SearchResult


class SearchRepositoryTests(unittest.TestCase):
    def make_repository(self, root: Path):
        database = Database(root / "app.sqlite3")
        database.create_schema()
        return database, SearchRepository(database)

    def test_search_run_persists_results_and_reuses_listing(self):
        with tempfile.TemporaryDirectory() as td:
            database, repository = self.make_repository(Path(td))
            profile = SearchProfile(
                advert_id="12345",
                name="Anna",
                city="Leipzig",
                category="huren-hostessen",
                profile_url="https://www.ladies.de/sex-anzeigen/12345",
                thumbnail_url="https://bilder1.ladies-cdn.de/example.jpg",
                has_video=True,
                markers={"tag": "new"},
            )
            run_id = repository.create_search_run(
                city="Leipzig",
                city_slug="leipzig",
                radius_km=20,
                filters={"category": "huren-hostessen"},
            )
            first = repository.finish_search_run(
                run_id,
                [profile],
                {"server_advert_count": 1, "distance_searched": 20},
            )
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0]["advert_id"], "12345")
            person_id = first[0]["person_id"]

            second_run = repository.create_search_run(
                city="Leipzig",
                city_slug="leipzig",
                radius_km=20,
                filters={"category": "huren-hostessen"},
            )
            second = repository.finish_search_run(second_run, [profile], {"server_advert_count": 1})
            self.assertEqual(second[0]["person_id"], person_id)

            with database.session() as db:
                self.assertEqual(db.scalar(select(func.count(Listing.listing_id))), 1)
                self.assertEqual(db.scalar(select(func.count(SearchResult.search_result_id))), 2)
                self.assertEqual(db.scalar(select(func.count(ListingObservation.observation_id))), 2)

    def test_recent_searches_are_deduplicated_by_city_and_radius(self):
        with tempfile.TemporaryDirectory() as td:
            _, repository = self.make_repository(Path(td))
            profile = SearchProfile(
                advert_id="1",
                name="A",
                city="Leipzig",
                category="huren-hostessen",
                profile_url="https://www.ladies.de/sex-anzeigen/1",
                thumbnail_url=None,
            )
            for radius in (20, 20, 30):
                run_id = repository.create_search_run(
                    city="Leipzig", city_slug="leipzig", radius_km=radius, filters={}
                )
                repository.finish_search_run(run_id, [profile], {})

            rows = repository.recent_searches(8)
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["radius_km"] for row in rows}, {20, 30})


if __name__ == "__main__":
    unittest.main()
