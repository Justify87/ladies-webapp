from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class NoNodeGateway:
    def __init__(self, worker_dir, auth_dir, event_handler):
        self.worker_dir = Path(worker_dir)
        self.auth_dir = Path(auth_dir)
        self.event_handler = event_handler
        self.proc = None
        self.state = "DEPENDENCY_MISSING"
        self.detail = "test"
        self.qr = None
        self.account = None
        self.last_connected_at = None

    def dependency_status(self):
        return False, "test"

    def status(self):
        return {
            "state": self.state,
            "detail": self.detail,
            "qr_available": False,
            "account": None,
            "last_connected_at": None,
        }

    def stop(self):
        pass


class MessagingHttpContractTests(unittest.TestCase):
    def make_app(self, td):
        from ladies_v9.app import create_app
        root = Path(td)
        return create_app(db_path=root / "app.sqlite3", data_dir=root / "data")

    def test_canonical_messaging_http_surface_is_registered(self):
        with tempfile.TemporaryDirectory() as td, patch(
            "ladies_v9.messaging.service.WhatsAppGateway", NoNodeGateway
        ):
            app = self.make_app(td)
            rules = {
                (rule.rule, tuple(sorted(method for method in rule.methods if method not in {"HEAD", "OPTIONS"})))
                for rule in app.url_map.iter_rules()
            }

        expected = {
            ("/api/messaging/status", ("GET",)),
            ("/api/messaging/threads", ("GET",)),
            ("/api/messaging/threads/<int:thread_id>", ("GET",)),
            ("/api/messaging/threads/<int:thread_id>/messages", ("POST",)),
            ("/api/messaging/threads/<int:thread_id>/media", ("POST",)),
            ("/api/messaging/messages/<int:message_id>/reaction", ("POST",)),
            ("/api/messaging/messages/<int:message_id>", ("DELETE", "PATCH")),
            ("/api/messaging/messages/<int:message_id>/view-once", ("GET",)),
        }
        for contract in expected:
            with self.subTest(contract=contract):
                self.assertIn(contract, rules)

    def test_legacy_whatsapp_archive_is_not_directly_exposed(self):
        with tempfile.TemporaryDirectory() as td, patch(
            "ladies_v9.messaging.service.WhatsAppGateway", NoNodeGateway
        ):
            client = self.make_app(td).test_client()
            response = client.get("/archive/whatsapp/media/unsafe.html")
            self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
