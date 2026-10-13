from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ladies_v9.app import BROWSER_CSP, _ExpectedStreamDisconnectFilter
from ladies_v9.core.services.profile import ProfileService
from ladies_v9.core.services.search import parse_search_request
from ladies_v9.messaging.gateway import WhatsAppGateway


ROOT = Path(__file__).resolve().parents[1]


class SearchRegressionTests(unittest.TestCase):
    def test_unscoped_meeting_filter_uses_ladies_category(self):
        params = parse_search_request(
            {
                "city": "Leipzig",
                "radius": 20,
                "category": "",
                "age_min": 18,
                "age_max": 22,
                "selected": {"geschlecht": ["weiblich"], "treff": ["besuchbar"]},
            },
            default_city="leipzig",
            default_radius=20,
        )
        self.assertEqual(params["category"], "huren-hostessen")
        self.assertEqual(params["radius"], 20)

    def test_explicit_category_is_not_overridden_by_meeting_filter(self):
        params = parse_search_request(
            {"category": "escort", "selected": {"treff": ["Hotelbesuche"]}},
            default_city="leipzig",
            default_radius=20,
        )
        self.assertEqual(params["category"], "escort")

    def test_unscoped_search_without_meeting_filter_stays_unscoped(self):
        params = parse_search_request(
            {"category": "", "selected": {"geschlecht": ["weiblich"]}},
            default_city="leipzig",
            default_radius=20,
        )
        self.assertEqual(params["category"], "")


class ProfileCacheRegressionTests(unittest.TestCase):
    @patch("ladies_v9.core.services.profile.parse_profile_html")
    @patch("ladies_v9.core.services.profile.ADVERT_ID_RE")
    @patch("ladies_v9.core.services.profile.http_get")
    def test_repeated_profile_load_reuses_recent_upstream_html(self, http_get, advert_re, parse_profile):
        repository = Mock()
        repository.listing_target.return_value = {
            "current_url": "https://www.ladies.de/sex-anzeigen/test-profil",
            "person_id": 7,
            "category": "Ladies",
        }
        repository.ingest_profile.return_value = {
            "identity": {"person_id": 7},
            "profile": {"media": []},
        }
        limiter = Mock()
        response = Mock(status_code=200, text="<html>profile</html>", url="https://www.ladies.de/sex-anzeigen/test-profil")
        response.raise_for_status.return_value = None
        http_get.return_value = response
        advert_re.search.return_value.group.return_value = "123"
        parse_profile.return_value = Mock()

        service = ProfileService(repository, Mock(), limiter)
        first = service.load("123")
        second = service.load("123")

        self.assertEqual(first["identity"]["person_id"], 7)
        self.assertEqual(second["identity"]["person_id"], 7)
        self.assertEqual(http_get.call_count, 1)
        self.assertEqual(limiter.wait.call_count, 1)
        self.assertEqual(repository.ingest_profile.call_count, 2)


class RuntimeRegressionTests(unittest.TestCase):
    def test_expected_sse_generator_exit_is_not_logged_as_error(self):
        exc = GeneratorExit()
        record = logging.LogRecord(
            name="ladies_explorer",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="REQUEST exception id=%s path=%s",
            args=("abc", "/api/messaging/events"),
            exc_info=(GeneratorExit, exc, None),
        )
        self.assertFalse(_ExpectedStreamDisconnectFilter().filter(record))

        other = logging.LogRecord(
            name="ladies_explorer",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="REQUEST exception id=%s path=%s",
            args=("abc", "/api/search"),
            exc_info=(GeneratorExit, exc, None),
        )
        self.assertTrue(_ExpectedStreamDisconnectFilter().filter(other))

    def test_browser_csp_blocks_odml_telemetry_but_keeps_mediapipe_sources(self):
        self.assertNotIn("odml.pa.googleapis.com", BROWSER_CSP)
        self.assertIn("https://cdn.jsdelivr.net", BROWSER_CSP)
        self.assertIn("https://storage.googleapis.com", BROWSER_CSP)

    def test_search_action_is_a_permanent_dock_above_navigation(self):
        css = (ROOT / "ladies" / "static" / "css" / "search_ui.css").read_text(encoding="utf-8")
        self.assertIn("width:min(100vw,430px)", css)
        self.assertIn("bottom:calc(var(--nav-h) + var(--safe-bottom))", css)
        self.assertIn("@media (min-width:431px)", css)
        self.assertIn("max(16px", css)

    def test_removed_messaging_stylesheet_is_not_imported(self):
        css = (ROOT / "ladies" / "static" / "css" / "chat_profile.css").read_text(encoding="utf-8")
        self.assertNotIn("messaging_complete.css", css)

    def test_preview_error_copy_is_distinct_from_zero_results(self):
        app_js = (ROOT / "ladies" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Trefferzahl nicht verfügbar", app_js)
        self.assertIn("previewCount=null;previewStatus='error'", app_js)


class WhatsAppRecoveryRegressionTests(unittest.TestCase):
    def test_missing_app_state_key_log_is_detected(self):
        line = json.dumps({
            "level": 40,
            "error": 'Error: failed to find key "AAAAALAw" to decode mutation',
        })
        self.assertTrue(WhatsAppGateway._is_app_state_missing_key_log(line))
        self.assertFalse(WhatsAppGateway._is_app_state_missing_key_log(json.dumps({"level": 40, "msg": "other"})))

    def test_app_state_recovery_preserves_credentials_and_sync_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            auth = root / "auth"
            auth.mkdir()
            version = auth / "app-state-sync-version-critical_block.json"
            key = auth / "app-state-sync-key-AAAA.json"
            creds = auth / "creds.json"
            version.write_text("{}", encoding="utf-8")
            key.write_text("{}", encoding="utf-8")
            creds.write_text("{}", encoding="utf-8")

            gateway = WhatsAppGateway(root / "worker", auth, lambda _event: None)
            removed = gateway._clear_app_state_versions()

            self.assertEqual(removed, 1)
            self.assertFalse(version.exists())
            self.assertTrue(key.exists())
            self.assertTrue(creds.exists())


if __name__ == "__main__":
    unittest.main()
