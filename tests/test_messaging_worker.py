from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ladies_v9.identity import normalize_phone_number
from ladies_v9.messaging.gateway import WhatsAppGateway


class WorkerManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.events = []
        self.manager = WhatsAppGateway(root / "worker", root / "auth", self.events.append)

    def tearDown(self):
        self.manager.stop()
        self.tmp.cleanup()

    def test_missing_node_is_nonfatal_dependency_state(self):
        with patch("ladies_v9.messaging.gateway.shutil.which", return_value=None):
            ok, detail = self.manager.dependency_status()
            self.assertFalse(ok)
            self.assertIn("Node.js 20+", detail)

    def test_connection_state_machine_rejects_unknown_state(self):
        self.manager._set_state("CONNECTED")
        self.assertEqual(self.manager.state, "CONNECTED")
        self.manager._set_state("invented")
        self.assertEqual(self.manager.state, "ERROR")

    def test_worker_log_masks_whatsapp_identifiers(self):
        masked = self.manager._safe_worker_log("jid=4917612345678@s.whatsapp.net")
        self.assertNotIn("4917612345678@s.whatsapp.net", masked)
        self.assertNotIn("4917612345678", masked)
        self.assertEqual(masked, "jid=491******5678@s.whatsapp.net")

    def test_worker_command_error_is_not_exposed_to_caller(self):
        secret_detail = "send failed for 4917612345678@s.whatsapp.net auth=/private/session"
        self.manager.proc = SimpleNamespace(poll=lambda: None, stdin=io.StringIO())
        with patch("ladies_v9.messaging.gateway.queue.Queue.get", return_value={"ok": False, "message": secret_detail}):
            with self.assertRaisesRegex(RuntimeError, "^WhatsApp-Aktion konnte nicht ausgeführt werden$") as raised:
                self.manager.command("send_text", jid="4917612345678@s.whatsapp.net", text="Hallo")
        self.assertNotIn("4917612345678", str(raised.exception))
        self.assertNotIn("/private/session", str(raised.exception))


class PhoneNormalizationTests(unittest.TestCase):
    def test_german_phone_normalization(self):
        self.assertEqual(normalize_phone_number("0176 12345678"), "+4917612345678")
        self.assertEqual(normalize_phone_number("0049 176 12345678"), "+4917612345678")
        self.assertEqual(normalize_phone_number("https://wa.me/4917612345678"), "+4917612345678")


if __name__ == "__main__":
    unittest.main()
