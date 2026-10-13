from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from ladies_v9.messaging import gateway
from ladies_v9.messaging.media_store import MessagingMediaStore


ROOT = Path(__file__).resolve().parents[1]


class MessagingArchitectureContractTests(unittest.TestCase):
    def test_gateway_has_one_canonical_public_implementation(self):
        self.assertTrue(hasattr(gateway, "WhatsAppGateway"))
        self.assertFalse(hasattr(gateway, "BaileysWorkerManager"))

    def test_media_store_never_resolves_outside_its_managed_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = MessagingMediaStore(root / "data")
            outside = root / "outside.bin"
            outside.write_bytes(b"outside")

            with self.assertRaises(FileNotFoundError):
                store.resolve("../outside.bin")

    def test_active_document_content_is_forced_to_download(self):
        with tempfile.TemporaryDirectory() as td:
            store = MessagingMediaStore(Path(td) / "data")
            document = store.media_dir / "active.html"
            document.write_text("<p>active content</p>", encoding="utf-8")

            path, mime, name, attachment = store.delivery(
                "DOCUMENT",
                {
                    "archive_name": document.name,
                    "mime_type": "text/html",
                    "file_name": "active.html",
                },
            )

            self.assertEqual(path, document.resolve())
            self.assertEqual(mime, "application/octet-stream")
            self.assertEqual(name, "active.html")
            self.assertTrue(attachment)

    def test_ffmpeg_dependency_is_declared_in_project_metadata(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        dependencies = metadata["project"]["dependencies"]
        self.assertEqual(
            sum(dep.startswith("imageio-ffmpeg") for dep in dependencies),
            1,
        )


if __name__ == "__main__":
    unittest.main()
