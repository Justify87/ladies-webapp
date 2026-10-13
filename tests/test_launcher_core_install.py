import json
import tempfile
import unittest
from pathlib import Path

from tools import launcher_core as core


class LauncherCoreInstallTests(unittest.TestCase):
    def _make_release(self, source: Path) -> None:
        (source / "ladies").mkdir(parents=True)
        (source / "tools").mkdir(parents=True)
        (source / "ladies_explorer.py").write_text("# test\n", encoding="utf-8")
        (source / "pyproject.toml").write_text("[project]\nname='test'\nversion='0.0.0'\n", encoding="utf-8")
        (source / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        (source / "tools" / "launcher_manager.py").write_text("# test\n", encoding="utf-8")
        (source / "tools" / "launcher_core.py").write_text("# test\n", encoding="utf-8")
        (source / "tools" / "launcher_manager.html").write_text("<html></html>\n", encoding="utf-8")
        (source / "release-manifest.json").write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "app_version": "9.8.0",
                    "tag": "v9.8.0",
                    "commit": "test",
                    "entrypoint": "ladies_explorer.py",
                    "db_schema_version": 0,
                }
            ),
            encoding="utf-8",
        )

    def test_install_does_not_recurse_when_source_is_install_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "LadiesExplorer"
            source.mkdir()
            self._make_release(source)

            # Reproduce the real-world failure: the extracted source directory already
            # has the default installation name, so source and install root are equal.
            (source / ".state").mkdir()
            (source / ".state" / "stale.txt").write_text("stale", encoding="utf-8")
            (source / "data").mkdir()
            (source / "data" / "stale.txt").write_text("stale", encoding="utf-8")

            tag = core.install(source, source)

            target = source / core.RELEASES_DIR / tag
            self.assertEqual(tag, "v9.8.0")
            self.assertTrue((target / "ladies_explorer.py").is_file())
            self.assertFalse((target / core.RELEASES_DIR).exists())
            self.assertFalse((target / core.DATA_DIR).exists())
            self.assertFalse((target / core.STATE_DIR).exists())
            self.assertFalse(any((source / core.RELEASES_DIR).glob(".staging-*")))
            self.assertEqual((source / core.CURRENT_FILE).read_text(encoding="utf-8").strip(), tag)


if __name__ == "__main__":
    unittest.main()
