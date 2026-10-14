from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ladies_v9.app import create_app


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "ladies" / "templates" / "index.html"
STATIC = ROOT / "ladies" / "static"
EXPLORER = STATIC / "explorer"


class ExplorerUiConsolidationTests(unittest.TestCase):
    def test_index_uses_only_v13_explorer_runtime(self):
        html = TEMPLATE.read_text(encoding="utf-8")
        for asset in (
            "/static/explorer/css/tokens.css",
            "/static/explorer/css/base.css",
            "/static/explorer/css/shell.css",
            "/static/explorer/css/components.css",
            "/static/explorer/css/screens.css",
            "/static/explorer/js/bootstrap.js",
        ):
            self.assertIn(asset, html)

        for legacy in (
            "/static/app.js",
            "/static/app.css",
            "/static/profile_messaging_ui.js",
            "/static/messaging.js",
            "/static/messaging.css",
            "/static/pricing.js",
            "/static/pricing.css",
            "/static/js/chat_profile.js",
        ):
            self.assertNotIn(legacy, html)

        for host_id in ("app", "appHeader", "screenHost", "bottomNav", "overlayHost", "toast"):
            self.assertIn(f'id="{host_id}"', html)

    def test_legacy_explorer_assets_are_removed(self):
        legacy_paths = (
            STATIC / "app.js",
            STATIC / "app.css",
            STATIC / "messaging.js",
            STATIC / "messaging.css",
            STATIC / "pricing.js",
            STATIC / "pricing.css",
            STATIC / "profile_messaging_ui.js",
            STATIC / "js" / "chat_profile.js",
            STATIC / "css" / "chat_profile.css",
            STATIC / "css" / "filter_ui.css",
            STATIC / "css" / "messaging_features.css",
            STATIC / "css" / "profile_messaging_ui.css",
            STATIC / "css" / "search_ui.css",
        )
        for path in legacy_paths:
            self.assertFalse(path.exists(), f"Legacy Explorer asset still exists: {path.relative_to(ROOT)}")

    def test_v13_runtime_is_modular_and_reuses_profile_component(self):
        bootstrap = (EXPLORER / "js" / "bootstrap.js").read_text(encoding="utf-8")
        render = (EXPLORER / "js" / "ui" / "render.js").read_text(encoding="utf-8")
        discover = (EXPLORER / "js" / "ui" / "screens" / "discover.js").read_text(encoding="utf-8")
        messaging = (EXPLORER / "js" / "ui" / "screens" / "messaging.js").read_text(encoding="utf-8")

        self.assertIn("createStore", bootstrap)
        self.assertIn("createRouter", bootstrap)
        self.assertIn("createEffects", bootstrap)
        self.assertIn("createController", bootstrap)
        self.assertIn("renderStandalone", render)
        self.assertIn("renderSwipe", discover)
        self.assertIn("../profile.js", discover)
        self.assertIn("/view-once", messaging)
        self.assertNotIn("<img src=\"/api/messaging/messages/${a(m.id)}/view-once", messaging)

    def test_package_data_includes_nested_explorer_assets(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        for pattern in (
            '"static/explorer/css/*.css"',
            '"static/explorer/css/components/*.css"',
            '"static/explorer/css/screens/*.css"',
            '"static/explorer/js/app/*.js"',
            '"static/explorer/js/effects/*.js"',
            '"static/explorer/js/ui/screens/*.js"',
        ):
            self.assertIn(pattern, pyproject)
        self.assertNotIn('"static/js/*.js"', pyproject)
        self.assertNotIn('"static/css/*.css"', pyproject)

    def test_index_injects_frontend_config(self):
        with tempfile.TemporaryDirectory() as td:
            app = create_app(db_path=Path(td) / "app.sqlite3", data_dir=Path(td) / "data")
            response = app.test_client().get("/")
            self.assertEqual(response.status_code, 200)
            body = response.get_data(as_text=True)
            self.assertIn("window.__LADIES_CONFIG__", body)
            self.assertIn('"defaults"', body)
            self.assertIn('"filter_groups"', body)


if __name__ == "__main__":
    unittest.main()
