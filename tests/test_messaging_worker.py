from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ladies_v9.identity import normalize_phone_number
from ladies_v9.messaging.whatsapp_manager import BaileysWorkerManager


class WorkerManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.events = []
        self.manager = BaileysWorkerManager(root / "worker", root / "auth", self.events.append)

    def tearDown(self):
        self.manager.stop()
        self.tmp.cleanup()

    def test_missing_node_is_nonfatal_dependency_state(self):
        with patch("ladies_v9.messaging.whatsapp_manager.shutil.which", return_value=None):
            ok, detail = self.manager.dependency_status()
            self.assertFalse(ok)
            self.assertIn("Node.js 20+", detail)

    def test_connection_state_machine_rejects_unknown_state(self):
        self.manager._set_state("CONNECTED")
        self.assertEqual(self.manager.state, "CONNECTED")
        self.manager._set_state("invented")
        self.assertEqual(self.manager.state, "ERROR")

    def test_worker_log_masks_long_numeric_identifiers(self):
        masked = self.manager._safe_worker_log("jid=4917612345678@s.whatsapp.net")
        self.assertNotIn("4917612345678", masked)
        self.assertIn("******", masked)


class PhoneNormalizationTests(unittest.TestCase):
    def test_german_phone_normalization(self):
        self.assertEqual(normalize_phone_number("0176 12345678"), "+4917612345678")
        self.assertEqual(normalize_phone_number("0049 176 12345678"), "+4917612345678")
        self.assertEqual(normalize_phone_number("https://wa.me/4917612345678"), "+4917612345678")


if __name__ == "__main__":
    unittest.main()
