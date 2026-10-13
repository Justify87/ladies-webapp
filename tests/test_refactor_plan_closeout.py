from __future__ import annotations

import unittest
from pathlib import Path

from ladies_v9.pricing.extraction_core import PriceExtractionCore
from ladies_v9.pricing.service import PriceIntelligenceService


ROOT = Path(__file__).resolve().parents[1]


class RefactorPlanCloseoutTests(unittest.TestCase):
    def test_pricing_service_is_single_owner_of_signal_and_accept(self):
        self.assertNotIn("signal", PriceExtractionCore.__dict__)
        self.assertNotIn("accept", PriceExtractionCore.__dict__)
        self.assertIn("signal", PriceIntelligenceService.__dict__)
        self.assertIn("accept", PriceIntelligenceService.__dict__)

    def test_prohibited_legacy_production_layers_are_absent(self):
        prohibited = (
            "ladies/repository.py",
            "ladies/messaging/service_complete.py",
            "ladies/messaging/service_reviewfix.py",
            "ladies/messaging/service_runtime.py",
            "ladies/static/js/runtime.js",
            "ladies/static/js/search_ui.js",
            "ladies/static/js/filter_ui.js",
            "ladies/static/ui_consolidation.js",
            "ladies/static/ui_followup.js",
            "ladies/static/messaging_bridge.js",
        )
        for relative in prohibited:
            with self.subTest(relative=relative):
                self.assertFalse((ROOT / relative).exists())

    def test_no_release_specific_pricing_parallel_service_exists(self):
        pricing = ROOT / "ladies" / "pricing"
        prohibited_names = {
            "service_hardened.py",
            "service_complete.py",
            "service_reviewfix.py",
            "service_runtime.py",
            "service_v92.py",
            "web_v92.py",
        }
        self.assertTrue(prohibited_names.isdisjoint({path.name for path in pricing.glob("*.py")}))


if __name__ == "__main__":
    unittest.main()
