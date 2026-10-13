from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ladies_v9.core.repositories.archive import ArchiveRepository
from ladies_v9.database import Database
from ladies_v9.media import ArchiveManager
from ladies_v9.models import MediaAsset


class ArchiveRepositoryTests(unittest.TestCase):
    def make_repository(self, root: Path):
        database = Database(root / "app.sqlite3")
        database.create_schema()
        archive = ArchiveManager(root / "data")
        return database, ArchiveRepository(database, archive)

    def test_media_listing_and_visual_analysis_are_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            database, repository = self.make_repository(root)
            with database.session() as db:
                media = MediaAsset(
                    sha256="a" * 64,
                    kind="IMAGE",
                    mime_type="image/jpeg",
                    byte_size=123,
                    width=100,
                    height=200,
                    local_path="media/example.jpg",
                )
                db.add(media)
                db.flush()
                media_id = int(media.media_asset_id)

            items = repository.items(kind="IMAGE")
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["media_asset_id"], media_id)
            self.assertEqual(items[0]["local_url"], "/archive/media/example.jpg")

            result = repository.save_visual_analysis(
                media_id,
                {"score": 0.8, "label": "likely-ai", "reasons": ["test"]},
            )
            self.assertEqual(result["label"], "likely-ai")
            self.assertEqual(result["reasons"], ["test"])
            self.assertEqual(repository.storage_info()["visual_analysis_count"], 1)
            self.assertEqual(repository.clear_visual_analyses(), 1)
            self.assertEqual(repository.storage_info()["visual_analysis_count"], 0)

    def test_visual_analysis_requires_known_media(self):
        with tempfile.TemporaryDirectory() as td:
            _, repository = self.make_repository(Path(td))
            with self.assertRaises(KeyError):
                repository.save_visual_analysis(999, {"score": 0.5})


if __name__ == "__main__":
    unittest.main()
