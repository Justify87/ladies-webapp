from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools import build_release


class ReleasePackageTests(unittest.TestCase):
    def test_build_contains_single_manager_runtime_and_excludes_persistent_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = build_release.build(Path(tmp), commit="test-commit")
            self.assertTrue(archive.name.startswith("ladies-explorer-v"))
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                self.assertIn("ladies_explorer.py", names)
                self.assertIn("pyproject.toml", names)
                self.assertIn("uv.lock", names)
                self.assertIn("tools/launcher_manager.py", names)
                self.assertIn("tools/launcher_core.py", names)
                self.assertIn("tools/launcher_manager.html", names)
                self.assertNotIn("tools/portable_manager.py", names)
                self.assertIn("packaging/start.command", names)
                self.assertIn("packaging/update.command", names)
                self.assertIn("release-manifest.json", names)
                self.assertNotIn("ladies.sqlite3", names)
                self.assertFalse(any(name.startswith("ladies_data/") for name in names))
                self.assertFalse(any(name.startswith("logs/") for name in names))
                self.assertFalse(any("node_modules" in name for name in names))
                self.assertFalse(any("/.venv/" in f"/{name}" for name in names))
                manifest = json.loads(bundle.read("release-manifest.json"))
                self.assertEqual(manifest["format_version"], 1)
                self.assertEqual(manifest["commit"], "test-commit")
                self.assertEqual(manifest["tag"], "v" + manifest["app_version"])
                self.assertEqual(manifest["entrypoint"], "ladies_explorer.py")
                self.assertIsInstance(manifest["db_schema_version"], int)

    def test_file_set_rejects_persistent_database(self):
        with self.assertRaises(RuntimeError):
            build_release._validate_file_set([Path("ladies.sqlite3")])

    def test_file_set_rejects_legacy_portable_manager(self):
        files = [
            Path("ladies_explorer.py"), Path("pyproject.toml"), Path("uv.lock"), Path("README.md"),
            Path("tools/launcher_manager.py"), Path("tools/launcher_core.py"), Path("tools/launcher_manager.html"),
            Path("tools/build_release.py"), Path("tools/portable_manager.py"),
            Path("packaging/install.command"), Path("packaging/start.command"), Path("packaging/update.command"),
            Path("packaging/install.cmd"), Path("packaging/start.cmd"), Path("packaging/update.cmd"),
        ]
        with self.assertRaisesRegex(RuntimeError, "Portable Manager"):
            build_release._validate_file_set(files)


if __name__ == "__main__":
    unittest.main()
