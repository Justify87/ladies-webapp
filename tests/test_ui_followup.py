from __future__ import annotations

import unittest
from pathlib import Path

from ladies_v9.config import frontend_config
from ladies_v9.source import CATEGORY_MAP, FILTER_GROUP_MAP, FILTER_VALUE_MAP


ROOT = Path(__file__).parents[1]


class UiFollowupTests(unittest.TestCase):
    def test_complete_v8_categories_are_preserved_in_backend_catalog(self):
        expected = {
            "huren-hostessen",
            "bordelle-laufhaeuser",
            "escort",
            "transen-shemale",
            "erotische-massage",
            "domina-sm",
            "fkk-saunaclubs",
            "swinger",
            "telefonsex",
            "onlineservice",
        }
        config = frontend_config(default_city="leipzig", default_radius=20)
        frontend_categories = {item["value"] for item in config["categories"]}
        for value in expected:
            with self.subTest(value=value):
                self.assertIn(value, CATEGORY_MAP)
                self.assertIn(value, frontend_categories)

    def test_complete_v8_filter_groups_are_exposed_by_backend_config(self):
        expected_groups = {
            "figur",
            "typ",
            "geschlecht",
            "busen",
            "intimbereich",
            "hautton",
            "haarfarbe",
            "haarlaenge",
            "augenfarbe",
            "koerperschmuck",
            "sonstiges",
            "service",
            "treff",
            "location",
            "zeiten",
        }
        config = frontend_config(default_city="leipzig", default_radius=20)
        frontend_groups = {item["key"] for item in config["filter_groups"]}
        for group in expected_groups:
            with self.subTest(group=group):
                self.assertIn(group, FILTER_GROUP_MAP)
                self.assertIn(group, FILTER_VALUE_MAP)
                self.assertIn(group, frontend_groups)

    def test_representative_values_from_every_v8_section_are_preserved(self):
        samples = {
            "figur": "sehr mollig",
            "typ": "osteuropäisch",
            "geschlecht": "Paar/Duo",
            "busen": "Naturbusen",
            "intimbereich": "stark behaart",
            "hautton": "bräunliche Haut",
            "haarfarbe": "dunkelbraun",
            "haarlaenge": "rückenlang",
            "augenfarbe": "blau-grün, grau",
            "koerperschmuck": "Gesichts-/Nabelp.",
            "sonstiges": "Mit 18+ Inhalten",
            "service": "Synchron-/Duo-M.",
            "treff": "Bürobesuche",
            "location": "für Rollstuhlfahrer geeignet",
            "zeiten": "auch Sonntag",
        }
        config = frontend_config(default_city="leipzig", default_radius=20)
        frontend = {
            group["key"]: {option["label"] for option in group["options"]}
            for group in config["filter_groups"]
        }
        for group, value in samples.items():
            with self.subTest(group=group, value=value):
                self.assertIn(value, FILTER_VALUE_MAP[group])
                self.assertIn(value, frontend[group])

    def test_legacy_dynamic_bridge_files_are_gone_from_template(self):
        index = (ROOT / "ladies" / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("messaging_bridge.js", index)
        self.assertNotIn("ui_followup.js", index)
        self.assertNotIn("ui_consolidation.js", index)


if __name__ == "__main__":
    unittest.main()
