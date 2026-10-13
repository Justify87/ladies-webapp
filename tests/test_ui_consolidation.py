from __future__ import annotations

import unittest
from pathlib import Path

from ladies_v9.config import frontend_config


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "ladies" / "static"
TEMPLATE = ROOT / "ladies" / "templates" / "index.html"


class UiConsolidationTests(unittest.TestCase):
    def test_template_uses_canonical_assets_without_transitional_runtime(self):
        index = TEMPLATE.read_text(encoding="utf-8")

        for asset in (
            "/static/app.js",
            "/static/profile_messaging_ui.js",
            "/static/messaging.js",
            "/static/js/chat_profile.js",
            "/static/pricing.js",
            "/static/css/profile_messaging_ui.css",
            "/static/css/messaging_features.css",
            "/static/css/search_ui.css",
            "/static/css/filter_ui.css",
            "/static/css/chat_profile.css",
        ):
            with self.subTest(asset=asset):
                self.assertIn(asset, index)

        for removed in (
            "/static/js/runtime.js",
            "/static/js/search_ui.js",
            "/static/js/filter_ui.js",
            "/static/ui_consolidation.js",
            "/static/ui_followup.js",
            "/static/messaging_bridge.js",
            "/static/css/messaging_complete.css",
        ):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, index)

    def test_transitional_frontend_files_are_deleted(self):
        for relative in (
            "js/runtime.js",
            "js/search_ui.js",
            "js/filter_ui.js",
            "ui_consolidation.js",
            "ui_followup.js",
            "messaging_bridge.js",
            "css/messaging_complete.css",
        ):
            with self.subTest(relative=relative):
                self.assertFalse((STATIC / relative).exists())

    def test_browser_entrypoints_are_real_es_modules(self):
        index = TEMPLATE.read_text(encoding="utf-8")
        for asset in (
            "/static/app.js",
            "/static/profile_messaging_ui.js",
            "/static/messaging.js",
            "/static/js/chat_profile.js",
            "/static/pricing.js",
        ):
            with self.subTest(asset=asset):
                self.assertIn(f'<script type="module" src="{asset}"></script>', index)

        app = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("export { state, api, toast", app)
        self.assertNotIn("window.LadiesApp", app)

    def test_feature_modules_import_explicit_app_contracts(self):
        profile_messaging = (STATIC / "profile_messaging_ui.js").read_text(encoding="utf-8")
        messaging = (STATIC / "messaging.js").read_text(encoding="utf-8")
        chat_profile = (STATIC / "js" / "chat_profile.js").read_text(encoding="utf-8")
        pricing = (STATIC / "pricing.js").read_text(encoding="utf-8")

        self.assertIn("from './app.js'", profile_messaging)
        self.assertIn("from './app.js'", messaging)
        self.assertIn("from '../app.js'", chat_profile)
        self.assertIn("from './app.js'", pricing)
        for source in (profile_messaging, messaging, chat_profile, pricing):
            self.assertNotIn("window.LadiesApp", source)

    def test_messaging_shell_is_static_and_has_single_owner(self):
        index = TEMPLATE.read_text(encoding="utf-8")
        messaging = (STATIC / "messaging.js").read_text(encoding="utf-8")
        profile_messaging = (STATIC / "profile_messaging_ui.js").read_text(encoding="utf-8")

        for marker in (
            'id="messagesScreen"',
            'id="moreScreen"',
            'id="messagingChatLayer"',
            'data-nav="messages"',
            'id="messageNavBadge"',
        ):
            self.assertIn(marker, index)

        self.assertNotIn("insertAdjacentHTML", messaging)
        self.assertNotIn("nav.innerHTML", messaging)
        self.assertNotIn("q('#menuBtn').onclick", messaging)
        self.assertNotIn("injectShell", messaging)
        self.assertIn("function bindShell()", messaging)

        self.assertNotIn("stopImmediatePropagation", profile_messaging)
        self.assertNotIn("cleanMessagesUi", profile_messaging)
        self.assertNotIn("observe(document.body", profile_messaging)
        self.assertNotIn(".profile-chat-section", profile_messaging)
        self.assertIn("ladies:open-person-chat", profile_messaging)
        self.assertIn("ladies:messaging-thread-opened", profile_messaging)
        self.assertIn("ladies:open-person-chat", messaging)

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

    def test_pricing_does_not_monkeypatch_fetch(self):
        pricing = (STATIC / "pricing.js").read_text(encoding="utf-8")
        self.assertNotIn("window.fetch =", pricing)
        self.assertNotIn("window.fetch=", pricing)
        self.assertIn("ladies:messaging-thread-opened", pricing)
        self.assertIn("ladies:messaging-thread-updated", pricing)
        self.assertIn("ladies:messaging-thread-closed", pricing)

    def test_messaging_styles_are_static_assets(self):
        index = TEMPLATE.read_text(encoding="utf-8")
        messaging = (STATIC / "messaging.js").read_text(encoding="utf-8")
        self.assertIn('/static/css/profile_messaging_ui.css', index)
        self.assertIn('/static/css/messaging_features.css', index)
        self.assertLess(
            index.index('/static/css/profile_messaging_ui.css'),
            index.index('/static/css/messaging_features.css'),
        )
        self.assertNotIn("messagingCompleteStyle", messaging)
        self.assertNotIn("document.createElement('style')", messaging)
        self.assertNotIn('document.createElement("style")', messaging)


if __name__ == "__main__":
    unittest.main()