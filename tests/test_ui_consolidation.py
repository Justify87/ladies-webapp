from __future__ import annotations

import unittest
from pathlib import Path

from ladies_v9.config import frontend_config


ROOT = Path(__file__).resolve().parents[1]


class UiConsolidationTests(unittest.TestCase):
    def test_template_loads_canonical_assets_without_legacy_bridge_layers(self):
        index = (ROOT / "ladies" / "templates" / "index.html").read_text(encoding="utf-8")
        for asset in (
            "/static/app.js",
            "/static/js/runtime.js",
            "/static/js/search_ui.js",
            "/static/js/filter_ui.js",
            "/static/js/chat_profile.js",
            "/static/css/search_ui.css",
            "/static/css/filter_ui.css",
            "/static/css/chat_profile.css",
            "/static/profile_messaging_ui.js",
            "/static/messaging.js",
            "/static/pricing.js",
        ):
            with self.subTest(asset=asset):
                self.assertIn(asset, index)
        for removed in (
            "/static/ui_consolidation.js",
            "/static/ui_followup.js",
            "/static/messaging_bridge.js",
        ):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, index)

    def test_backend_config_is_filter_and_version_source_of_truth(self):
        config = frontend_config(default_city="leipzig", default_radius=20)
        self.assertEqual(config["defaults"], {"city": "leipzig", "radius_km": 20})
        self.assertTrue(config["version"].startswith("9.6"))

        categories = {item["value"] for item in config["categories"]}
        self.assertIn("huren-hostessen", categories)
        self.assertIn("bordelle-laufhaeuser", categories)
        self.assertIn("onlineservice", categories)

        groups = {item["key"]: item["options"] for item in config["filter_groups"]}
        self.assertIn("service", groups)
        service_labels = {item["label"] for item in groups["service"]}
        self.assertIn("Messebegleitung", service_labels)
        self.assertIn("Dusch-/Badeservice", service_labels)

    def test_template_receives_boot_config_before_feature_scripts(self):
        index = (ROOT / "ladies" / "templates" / "index.html").read_text(encoding="utf-8")
        config_pos = index.index("window.__LADIES_CONFIG__")
        app_pos = index.index('/static/app.js')
        runtime_pos = index.index('/static/js/runtime.js')
        search_pos = index.index('/static/js/search_ui.js')
        filter_pos = index.index('/static/js/filter_ui.js')
        self.assertLess(config_pos, app_pos)
        self.assertLess(app_pos, runtime_pos)
        self.assertLess(runtime_pos, search_pos)
        self.assertLess(search_pos, filter_pos)


if __name__ == "__main__":
    unittest.main()
