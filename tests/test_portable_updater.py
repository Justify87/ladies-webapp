from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest import mock

from tools import launcher_core as core


def make_release(path: Path, version: str, schema: int = 1) -> Path:
    tag = f"v{version}"
    path.mkdir(parents=True, exist_ok=True)
    (path / "ladies").mkdir()
    (path / "ladies" / "__init__.py").write_text("\n", encoding="utf-8")
    (path / "tools").mkdir()
    (path / "packaging").mkdir()
    (path / "ladies_explorer.py").write_text("print('ok')\n", encoding="utf-8")
    (path / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
    (path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (path / "tools" / "launcher_manager.py").write_text("# manager\n", encoding="utf-8")
    (path / "tools" / "launcher_core.py").write_text("# core\n", encoding="utf-8")
    (path / "tools" / "launcher_manager.html").write_text("<html>manager</html>\n", encoding="utf-8")
    for name in ("start.command", "update.command", "start.cmd", "update.cmd"):
        (path / "packaging" / name).write_text(f"launcher-{version}\n", encoding="utf-8")
    (path / "release-manifest.json").write_text(json.dumps({
        "format_version": 1,
        "app_version": version,
        "tag": tag,
        "commit": "deadbeef",
        "entrypoint": "ladies_explorer.py",
        "db_schema_version": schema,
    }), encoding="utf-8")
    return path


def zip_release(source: Path, archive: Path) -> str:
    with zipfile.ZipFile(archive, "w") as bundle:
        for file in source.rglob("*"):
            if file.is_file():
                bundle.write(file, file.relative_to(source).as_posix())
    return hashlib.sha256(archive.read_bytes()).hexdigest()


def release_payload(version: str, digest: str, *, body: str = "notes") -> dict:
    return {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": False,
        "body": body,
        "assets": [{
            "name": f"ladies-explorer-v{version}.zip",
            "browser_download_url": "https://example.invalid/release.zip",
            "digest": "sha256:" + digest,
        }],
    }


class LauncherCoreTests(unittest.TestCase):
    def test_semver_is_strict(self):
        self.assertEqual(core._semver("v9.6.3"), (9, 6, 3))
        with self.assertRaises(core.PortableError):
            core._semver("9.6")

    def test_process_running_probe_is_read_only_for_current_process(self):
        self.assertTrue(core._process_running(os.getpid()))

    def test_lifecycle_lock_blocks_concurrent_action_and_recovers_stale_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with core.lifecycle_lock(root, "outer"):
                with self.assertRaises(core.PortableError):
                    with core.lifecycle_lock(root, "inner"):
                        self.fail("concurrent lifecycle action unexpectedly acquired the lock")
            lock = root / core.STATE_DIR / core.LIFECYCLE_LOCK_FILE
            lock.write_text("999999999\nstale\n", encoding="ascii")
            with mock.patch.object(core, "_process_running", return_value=False):
                with core.lifecycle_lock(root, "new"):
                    self.assertTrue(lock.exists())
            self.assertFalse(lock.exists())

    def test_safe_extract_rejects_parent_traversal_on_both_path_styles(self):
        for member in ("../escape.txt", "..\\escape.txt"):
            with self.subTest(member=member), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = root / "bad.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    bundle.writestr(member, "bad")
                with self.assertRaises(core.PortableError):
                    core.safe_extract(archive, root / "out")

    def test_manifest_requires_matching_version_and_single_manager_architecture(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = make_release(Path(tmp) / "release", "9.8.0")
            self.assertEqual(core.validate_release_dir(release)["tag"], "v9.8.0")
            self.assertFalse((release / "tools" / "portable_manager.py").exists())
            manifest_path = release / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["tag"] = "v9.8.1"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(core.PortableError):
                core.validate_release_dir(release)

    def test_digest_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "release.zip"
            path.write_bytes(b"payload")
            with self.assertRaises(core.PortableError):
                core.verify_asset_digest(path, {"digest": "sha256:" + "0" * 64})

    def test_legacy_migration_requires_stopped_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy = base / "legacy"
            (legacy / "ladies_data").mkdir(parents=True)
            with self.assertRaises(core.PortableError):
                core.migrate_legacy_data(legacy, base / "install")

    def test_install_separates_release_and_persistent_data_with_sqlite_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = make_release(base / "source", "9.8.0")
            legacy = base / "legacy"
            (legacy / "ladies_data" / "whatsapp" / "auth").mkdir(parents=True)
            (legacy / "ladies_data" / "whatsapp" / "auth" / "creds.json").write_text("secret", encoding="utf-8")
            legacy_db = legacy / "ladies.sqlite3"
            with closing(sqlite3.connect(legacy_db)) as connection:
                connection.execute("CREATE TABLE sample(value TEXT NOT NULL)")
                connection.execute("INSERT INTO sample(value) VALUES ('preserved')")
                connection.execute("PRAGMA user_version = 7")
                connection.commit()
            root = base / "LadiesExplorer"

            core.install(source, root, legacy, legacy_stopped=True)

            self.assertEqual(core.read_current(root), "v9.8.0")
            self.assertTrue((root / "releases" / "v9.8.0" / "tools" / "launcher_core.py").is_file())
            self.assertFalse((root / "releases" / "v9.8.0" / "tools" / "portable_manager.py").exists())
            self.assertEqual((root / "data" / "ladies_data" / "whatsapp" / "auth" / "creds.json").read_text(), "secret")
            with closing(sqlite3.connect(root / "data" / "ladies.sqlite3")) as connection:
                self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], "preserved")
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 7)

    def test_update_keeps_current_when_digest_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            make_release(root / "releases" / "v9.8.0", "9.8.0")
            core.set_current(root, "v9.8.0")
            candidate = make_release(Path(tmp) / "candidate", "9.8.1")
            archive = Path(tmp) / "candidate.zip"
            zip_release(candidate, archive)
            release = release_payload("9.8.1", "0" * 64)

            def fake_download(_url, destination):
                destination.write_bytes(archive.read_bytes())

            with mock.patch.object(core, "latest_release", return_value=release), mock.patch.object(
                core, "_download", side_effect=fake_download
            ):
                with self.assertRaises(core.PortableError):
                    core.update_release(root)
            self.assertEqual(core.read_current(root), "v9.8.0")
            self.assertFalse((root / "releases" / "v9.8.1").exists())

    def test_successful_update_activates_verified_release_and_preserves_previous(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            make_release(root / "releases" / "v9.8.0", "9.8.0")
            core.set_current(root, "v9.8.0")
            candidate = make_release(Path(tmp) / "candidate", "9.8.1")
            archive = Path(tmp) / "candidate.zip"
            digest = zip_release(candidate, archive)
            release = release_payload("9.8.1", digest)

            def fake_download(_url, destination):
                destination.write_bytes(archive.read_bytes())

            with mock.patch.object(core, "latest_release", return_value=release), mock.patch.object(
                core, "_download", side_effect=fake_download
            ):
                self.assertEqual(core.update_release(root), "v9.8.1")
            self.assertEqual(core.read_current(root), "v9.8.1")
            self.assertTrue((root / "releases" / "v9.8.0").exists())
            self.assertTrue((root / "releases" / "v9.8.1").exists())

    def test_restore_release_restores_current_and_root_launchers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            old = make_release(root / "releases" / "v9.8.0", "9.8.0")
            new = make_release(root / "releases" / "v9.8.1", "9.8.1")
            core.set_current(root, "v9.8.1")
            core._copy_launchers(new, root)
            self.assertEqual((root / "start.command").read_text(), "launcher-9.8.1\n")

            core.restore_release(root, "v9.8.0")

            self.assertEqual(core.read_current(root), "v9.8.0")
            self.assertEqual((root / "start.command").read_text(), "launcher-9.8.0\n")
            self.assertTrue(old.is_dir())


if __name__ == "__main__":
    unittest.main()
