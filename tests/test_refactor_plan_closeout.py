from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from flask import Flask

from ladies_v9.messaging import MessagingService, install_messaging
from ladies_v9.pricing import PriceIntelligenceService, install_pricing


class RefactorPlanCloseoutTests(unittest.TestCase):
    def test_feature_installers_require_the_application_database(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / "data"

            with self.assertRaisesRegex(RuntimeError, "gemeinsame Application-Database"):
                install_messaging(Flask("messaging-closeout"), data_dir=data_dir)

            with self.assertRaisesRegex(RuntimeError, "gemeinsame Application-Database"):
                install_pricing(Flask("pricing-closeout"), data_dir=data_dir)

    def test_services_reject_feature_local_database_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            with self.assertRaisesRegex(TypeError, "gemeinsame Application-Database"):
                MessagingService(root / "messaging.sqlite3", root / "data")  # type: ignore[arg-type]

            with self.assertRaisesRegex(TypeError, "gemeinsame Application-Database"):
                PriceIntelligenceService(root / "pricing.sqlite3", root / "data")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
