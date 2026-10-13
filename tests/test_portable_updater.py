from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tools import portable_manager as pm


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
    (path / "tools" / "portable_manager.py").write_text("# manager\n", encoding="utf-8")
    for name in ("start.command", "update.command", "start.cmd", "update.cmd"):
        (path / "packaging" / name).write_text("launcher\n", encoding="utf-8")
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


class PortableUpdaterTests(unittest.TestCase):
    def test_semver_is_strict(self):
        self.assertEqual(pm._semver("v9.6.3"), (9, 6, 3))
        with self.assertRaises(pm.PortableError):
            pm._semver("9.6")

    def test_process_running_probe_is_read_only_for_current_process(self):
        self.assertTrue(pm._process_running(os.getpid()))

    def test_lifecycle_lock_blocks_concurrent_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with pm.lifecycle_lock(root, "outer"):
                with self.assertRaises(pm.PortableError):
                    with pm.lifecycle_lock(root, "inner"):
                        self.fail("concurrent lifecycle action unexpectedly acquired the lock")

    def test_stale_lifecycle_lock_is_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / pm.STATE_DIR / pm.LIFECYCLE_LOCK_FILE
            lock.parent.mkdir(parents=True)
            lock.write_text("999999999\nstale\n", encoding="ascii")
            with mock.patch.object(pm, "_process_running", return_value=False):
                with pm.lifecycle_lock(root, "new"):
                    self.assertTrue(lock.exists())
            self.assertFalse(lock.exists())

    def test_safe_extract_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../escape.txt", "bad")
            with self.assertRaises(pm.PortableError):
                pm.safe_extract(archive, root / "out")
            self.assertFalse((root / "escape.txt").exists())

    def test_safe_extract_rejects_windows_parent_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad-windows.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("..\\escape.txt", "bad")
            with self.assertRaises(pm.PortableError):
                pm.safe_extract(archive, root / "out")

    def test_manifest_requires_matching_version_and_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = make_release(Path(tmp) / "release", "9.6.4")
            manifest_path = release / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["tag"] = "v9.6.5"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(pm.PortableError):
                pm.validate_release_dir(release)

    def test_digest_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "release.zip"
            path.write_bytes(b"payload")
            with self.assertRaises(pm.PortableError):
                pm.verify_asset_digest(path, {"digest": "sha256:" + "0" * 64})

    def test_legacy_migration_requires_stopped_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy = base / "legacy"
            (legacy / "ladies_data").mkdir(parents=True)
            with self.assertRaises(pm.PortableError):
                pm.migrate_legacy_data(legacy, base / "install")

    def test_legacy_migration_rejects_known_running_portable_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy = base / "legacy"
            state = legacy / pm.STATE_DIR
            state.mkdir(parents=True)
            (state / pm.PID_FILE).write_text(f"{os.getpid()}\n", encoding="ascii")
            with self.assertRaises(pm.PortableError):
                pm.migrate_legacy_data(legacy, base / "install", confirmed_stopped=True)

    def test_install_separates_release_and_persistent_data_with_sqlite_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = make_release(base / "source", "9.6.4")
            legacy = base / "legacy"
            (legacy / "ladies_data" / "whatsapp" / "auth").mkdir(parents=True)
            (legacy / "ladies_data" / "whatsapp" / "auth" / "creds.json").write_text("secret", encoding="utf-8")
            (legacy / "ladies_data" / "lan_access.json").write_text("session", encoding="utf-8")
            legacy_db = legacy / "ladies.sqlite3"
            with sqlite3.connect(legacy_db) as connection:
                connection.execute("CREATE TABLE sample(value TEXT NOT NULL)")
                connection.execute("INSERT INTO sample(value) VALUES ('preserved')")
                connection.execute("PRAGMA user_version = 7")
            root = base / "LadiesExplorer"

            pm.install(source, root, legacy, legacy_stopped=True)

            self.assertEqual(pm.read_current(root), "v9.6.4")
            self.assertTrue((root / "releases" / "v9.6.4" / "ladies_explorer.py").is_file())
            self.assertEqual((root / "data" / "ladies_data" / "whatsapp" / "auth" / "creds.json").read_text(), "secret")
            self.assertEqual((root / "data" / "ladies_data" / "lan_access.json").read_text(), "session")
            self.assertFalse((root / "releases" / "v9.6.4" / "ladies_data").exists())
            migrated_db = root / "data" / "ladies.sqlite3"
            with sqlite3.connect(migrated_db) as connection:
                self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], "preserved")
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 7)
            self.assertFalse((root / "data" / "ladies.sqlite3-wal").exists())
            self.assertFalse((root / "data" / "ladies.sqlite3-shm").exists())

    def test_update_keeps_current_when_digest_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            make_release(root / "releases" / "v9.6.3", "9.6.3")
            pm.set_current(root, "v9.6.3")
            root.joinpath("data", "ladies_data", "whatsapp", "auth").mkdir(parents=True)
            creds = root / "data" / "ladies_data" / "whatsapp" / "auth" / "creds.json"
            creds.write_text("keep-me", encoding="utf-8")
            candidate = make_release(Path(tmp) / "candidate", "9.6.4")
            archive = Path(tmp) / "candidate.zip"
            zip_release(candidate, archive)
            release = release_payload("9.6.4", "0" * 64)

            def fake_download(_url, destination):
                destination.write_bytes(archive.read_bytes())

            with mock.patch.object(pm, "latest_release", return_value=release), mock.patch.object(pm, "_download", side_effect=fake_download):
                with self.assertRaises(pm.PortableError):
                    pm.update(root)

            self.assertEqual(pm.read_current(root), "v9.6.3")
            self.assertEqual(creds.read_text(), "keep-me")
            self.assertFalse((root / "releases" / "v9.6.4").exists())

    def test_successful_update_activates_release_without_touching_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            make_release(root / "releases" / "v9.6.3", "9.6.3")
            pm.set_current(root, "v9.6.3")
            data = root / "data" / "ladies_data" / "whatsapp" / "auth"
            data.mkdir(parents=True)
            creds = data / "creds.json"
            creds.write_text("keep-me", encoding="utf-8")
            candidate = make_release(Path(tmp) / "candidate", "9.6.4")
            archive = Path(tmp) / "candidate.zip"
            digest = zip_release(candidate, archive)
            release = release_payload("9.6.4", digest)

            def fake_download(_url, destination):
                destination.write_bytes(archive.read_bytes())

            with mock.patch.object(pm, "latest_release", return_value=release), mock.patch.object(pm, "_download", side_effect=fake_download):
                self.assertEqual(pm.update(root), "v9.6.4")

            self.assertEqual(pm.read_current(root), "v9.6.4")
            self.assertEqual(creds.read_text(), "keep-me")
            self.assertTrue((root / "releases" / "v9.6.3").exists())
            self.assertTrue((root / "releases" / "v9.6.4").exists())

    def test_update_replaces_preexisting_inactive_target_with_verified_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            make_release(root / "releases" / "v9.6.3", "9.6.3")
            stale = make_release(root / "releases" / "v9.6.4", "9.6.4")
            (stale / "stale.txt").write_text("old", encoding="utf-8")
            pm.set_current(root, "v9.6.3")
            candidate = make_release(Path(tmp) / "candidate", "9.6.4")
            (candidate / "fresh.txt").write_text("new", encoding="utf-8")
            archive = Path(tmp) / "candidate.zip"
            digest = zip_release(candidate, archive)
            release = release_payload("9.6.4", digest)

            def fake_download(_url, destination):
                destination.write_bytes(archive.read_bytes())

            with mock.patch.object(pm, "latest_release", return_value=release), mock.patch.object(pm, "_download", side_effect=fake_download):
                pm.update(root)

            target = root / "releases" / "v9.6.4"
            self.assertTrue((target / "fresh.txt").exists())
            self.assertFalse((target / "stale.txt").exists())

    def test_update_retention_preserves_previous_active_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "LadiesExplorer"
            make_release(root / "releases" / "v9.6.3", "9.6.3")
            make_release(root / "releases" / "v9.6.4", "9.6.4")
            pm.set_current(root, "v9.6.3")
            candidate = make_release(Path(tmp) / "candidate", "9.6.5")
            archive = Path(tmp) / "candidate.zip"
            digest = zip_release(candidate, archive)
            release = release_payload("9.6.5", digest)

            def fake_download(_url, destination):
                destination.write_bytes(archive.read_bytes())

            with mock.patch.object(pm, "latest_release", return_value=release), mock.patch.object(pm, "_download", side_effect=fake_download):
                pm.update(root)

            self.assertEqual(pm.read_current(root), "v9.6.5")
            self.assertTrue((root / "releases" / "v9.6.3").exists())
            self.assertFalse((root / "releases" / "v9.6.4").exists())
            self.assertTrue((root / "releases" / "v9.6.5").exists())

    def test_rollback_refuses_newer_database_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_release(root / "releases" / "v9.6.3", "9.6.3", schema=1)
            make_release(root / "releases" / "v9.7.0", "9.7.0", schema=2)
            pm.set_current(root, "v9.7.0")
            db = root / "data" / "ladies.sqlite3"
            db.parent.mkdir(parents=True)
            with sqlite3.connect(db) as connection:
                connection.execute("PRAGMA user_version = 2")
            with self.assertRaises(pm.PortableError):
                pm.rollback(root)
            self.assertEqual(pm.read_current(root), "v9.7.0")

    def test_rollback_never_moves_forward(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_release(root / "releases" / "v9.6.3", "9.6.3")
            make_release(root / "releases" / "v9.6.4", "9.6.4")
            pm.set_current(root, "v9.6.3")
            with self.assertRaises(pm.PortableError):
                pm.rollback(root)
            self.assertEqual(pm.read_current(root), "v9.6.3")

    def test_prune_keeps_current_and_one_latest_release_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for version in ("9.6.2", "9.6.3", "9.6.4"):
                make_release(root / "releases" / f"v{version}", version)
            pm.set_current(root, "v9.6.4")
            pm.prune_releases(root)
            self.assertFalse((root / "releases" / "v9.6.2").exists())
            self.assertTrue((root / "releases" / "v9.6.3").exists())
            self.assertTrue((root / "releases" / "v9.6.4").exists())


if __name__ == "__main__":
    unittest.main()
