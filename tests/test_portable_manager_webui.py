from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from unittest import mock

from tools import launcher_manager as lm
from tools import portable_manager as pm


ROOT = Path(__file__).resolve().parents[1]


def make_release(path: Path, version: str, schema: int = 1, *, with_manager: bool = True) -> Path:
    tag = f"v{version}"
    path.mkdir(parents=True, exist_ok=True)
    (path / "ladies").mkdir()
    (path / "ladies" / "__init__.py").write_text("\n", encoding="utf-8")
    (path / "tools").mkdir()
    (path / "ladies_explorer.py").write_text("print('ok')\n", encoding="utf-8")
    (path / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
    (path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (path / "tools" / "portable_manager.py").write_text("# manager\n", encoding="utf-8")
    if with_manager:
        (path / "tools" / "launcher_manager.py").write_text("# launcher manager\n", encoding="utf-8")
        (path / "tools" / "launcher_manager.html").write_text("<html>manager</html>\n", encoding="utf-8")
    (path / "release-manifest.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "app_version": version,
                "tag": tag,
                "commit": "deadbeef",
                "entrypoint": "ladies_explorer.py",
                "db_schema_version": schema,
            }
        ),
        encoding="utf-8",
    )
    return path


def make_root(base: Path, version: str = "9.8.0", schema: int = 1) -> Path:
    root = base / "LadiesExplorer"
    make_release(root / "releases" / f"v{version}", version, schema)
    pm.set_current(root, version)
    db = root / "data" / "ladies.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as connection:
        connection.execute(f"PRAGMA user_version = {schema}")
        connection.commit()
    return root


class ManagerWebUiTests(unittest.TestCase):
    def test_status_reports_release_process_and_data_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(Path(tmp))
            controller = lm.Controller(root)
            with mock.patch.object(pm, "active_pid", return_value=4242), mock.patch.object(
                lm.shutil, "which", return_value="/usr/bin/uv"
            ):
                status = controller.status()
            self.assertEqual(status["version"], "v9.8.0")
            self.assertTrue(status["running"])
            self.assertEqual(status["pid"], 4242)
            self.assertEqual(status["db_schema"], 1)
            self.assertEqual(status["release_schema"], 1)
            self.assertTrue(status["uv_available"])

    def test_manager_api_requires_per_launch_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(Path(tmp))
            controller = lm.Controller(root)
            server = lm.ThreadingHTTPServer((lm.MANAGER_HOST, 0), lm.make_handler(controller, "secret-token", "<html>ok</html>"))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://{lm.MANAGER_HOST}:{server.server_port}"
            try:
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(base + "/api/ping", timeout=2)
                self.assertEqual(denied.exception.code, 403)

                request = urllib.request.Request(
                    base + "/api/ping", headers={"X-Ladies-Manager-Token": "secret-token"}
                )
                with urllib.request.urlopen(request, timeout=2) as response:
                    self.assertEqual(response.status, 200)
                    self.assertTrue(json.load(response)["ok"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_update_check_does_not_stop_running_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(Path(tmp), "9.8.0")
            controller = lm.Controller(root)
            release = {"tag_name": "v9.8.1", "draft": False, "prerelease": False, "body": "notes"}
            with mock.patch.object(pm, "latest_release", return_value=release), mock.patch.object(
                lm, "stop_app"
            ) as stop:
                controller.check_update()
            stop.assert_not_called()
            self.assertTrue(controller.status()["latest"]["available"])

    def test_managed_update_stops_migrates_and_restarts_running_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(Path(tmp), "9.8.0")
            make_release(root / "releases" / "v9.8.1", "9.8.1")
            controller = lm.Controller(root)
            release = {"tag_name": "v9.8.1", "draft": False, "prerelease": False, "body": "notes"}

            def activate(_root):
                pm.set_current(controller.root, "v9.8.1")
                return "v9.8.1"

            with mock.patch.object(pm, "latest_release", return_value=release), mock.patch.object(
                pm, "active_pid", return_value=222
            ), mock.patch.object(lm, "stop_app") as stop, mock.patch.object(
                pm, "update", side_effect=activate
            ), mock.patch.object(lm, "_copy_manager_to_state"), mock.patch.object(
                lm, "ensure_release_environment", return_value=Path("python")
            ), mock.patch.object(lm, "migrate_database", return_value=1) as migrate, mock.patch.object(
                lm, "start_app"
            ) as start:
                controller.apply_update()

            stop.assert_called_once()
            migrate.assert_called_once_with(controller.root, "v9.8.1", controller.emit)
            start.assert_called_once_with(controller.root, controller.emit, open_browser=True)
            self.assertEqual(pm.read_current(controller.root), "v9.8.1")
            self.assertTrue(any((controller.root / pm.STATE_DIR / "backups").glob("pre-update-*.sqlite3")))

    def test_failed_migration_restores_database_and_previous_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(Path(tmp), "9.8.0")
            make_release(root / "releases" / "v9.8.1", "9.8.1", schema=2)
            controller = lm.Controller(root)
            db = controller.root / "data" / "ladies.sqlite3"
            with closing(sqlite3.connect(db)) as connection:
                connection.execute("CREATE TABLE keep_me(value TEXT)")
                connection.execute("INSERT INTO keep_me(value) VALUES ('original')")
                connection.commit()
            release = {"tag_name": "v9.8.1", "draft": False, "prerelease": False, "body": "notes"}

            def activate(_root):
                pm.set_current(controller.root, "v9.8.1")
                with closing(sqlite3.connect(db)) as connection:
                    connection.execute("UPDATE keep_me SET value='changed'")
                    connection.commit()
                return "v9.8.1"

            with mock.patch.object(pm, "latest_release", return_value=release), mock.patch.object(
                pm, "active_pid", return_value=None
            ), mock.patch.object(pm, "update", side_effect=activate), mock.patch.object(
                lm, "_copy_manager_to_state"
            ), mock.patch.object(lm, "ensure_release_environment", return_value=Path("python")), mock.patch.object(
                lm, "migrate_database", side_effect=RuntimeError("migration failed")
            ):
                with self.assertRaises(RuntimeError):
                    controller.apply_update()

            self.assertEqual(pm.read_current(controller.root), "v9.8.0")
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(connection.execute("SELECT value FROM keep_me").fetchone()[0], "original")

    def test_copy_manager_to_state_keeps_script_and_ui_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(Path(tmp), "9.8.0")
            release = root / "releases" / "v9.8.0"
            manager = lm._copy_manager_to_state(root, release)
            expected = root / pm.STATE_DIR / lm.MANAGER_COPY
            self.assertEqual(manager.resolve(), expected.resolve())
            self.assertTrue(manager.is_file())
            self.assertTrue((manager.parent / lm.MANAGER_UI).is_file())

    def test_manager_ui_contains_core_controls(self):
        html = (ROOT / "tools" / "launcher_manager.html").read_text(encoding="utf-8")
        for label in ("Starten", "Stoppen", "App öffnen", "Update prüfen", "Aktualisieren", "Datenordner öffnen"):
            with self.subTest(label=label):
                self.assertIn(label, html)
        self.assertIn("Manager nur lokal erreichbar", html)


if __name__ == "__main__":
    unittest.main()
