from __future__ import annotations

import unittest
from pathlib import Path

from ladies_v9.source import CATEGORY_MAP, FILTER_GROUP_MAP, FILTER_VALUE_MAP


ROOT = Path(__file__).parents[1]
UI = ROOT / "ladies" / "static" / "ui_followup.js"
BRIDGE = ROOT / "ladies" / "static" / "messaging_bridge.js"


class UiFollowupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = UI.read_text(encoding="utf-8")
        cls.bridge = BRIDGE.read_text(encoding="utf-8")

    def test_chat_header_opens_profile_as_nested_layer(self):
        self.assertIn("#chatAvatar,#chatIdentity", self.source)
        self.assertIn("openProfileFromChat", self.source)
        self.assertIn("chat-profile-open", self.source)
        self.assertIn("body.chat-profile-open #profileLayer{z-index:180", self.source)
        self.assertIn("[data-profile-close]", self.source)
        self.assertIn("profileLayer.scrollTop = 0", self.source)

    def test_today_online_badge_is_removed_and_swipe_badges_do_not_wrap(self):
        self.assertIn(".swipe-title-line .online-pill,.profile-name-row>.online-pill{display:none!important}", self.source)
        self.assertIn("flex-wrap:nowrap!important", self.source)
        self.assertIn("overflow-x:auto!important", self.source)

    def test_age_and_height_each_use_one_dual_thumb_track(self):
        self.assertIn("followup-dual-range", self.source)
        self.assertIn("dualRange('age', 'Alter', 18, 60", self.source)
        self.assertIn("dualRange('height', 'Größe', 140, 200", self.source)
        self.assertIn("ageMinRange", self.source)
        self.assertIn("ageMaxRange", self.source)
        self.assertIn("heightMinRange", self.source)
        self.assertIn("heightMaxRange", self.source)

    def test_complete_v8_categories_are_restored_and_supported(self):
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
        for value in expected:
            with self.subTest(value=value):
                self.assertIn(f"['{value}',", self.source)
                self.assertIn(value, CATEGORY_MAP)

    def test_complete_v8_filter_groups_are_exposed_and_supported(self):
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
        for group in expected_groups:
            with self.subTest(group=group):
                self.assertIn(group, FILTER_GROUP_MAP)
                self.assertIn(group, FILTER_VALUE_MAP)

    def test_representative_values_from_every_v8_section_are_in_ui_and_backend(self):
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
        for group, value in samples.items():
            with self.subTest(group=group, value=value):
                self.assertIn(value, self.source)
                self.assertIn(value, FILTER_VALUE_MAP[group])

    def test_followup_script_loads_after_messaging_profile_ui(self):
        self.assertIn("/static/profile_messaging_ui.js", self.bridge)
        self.assertIn("/static/ui_followup.js", self.bridge)
        self.assertIn("profileUi.addEventListener('load', loadFollowup", self.bridge)


if __name__ == "__main__":
    unittest.main()
