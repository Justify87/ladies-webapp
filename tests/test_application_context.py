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


class ApplicationContextTests(unittest.TestCase):
    def make_app(self, td):
        from ladies_v9.app import create_app

        root = Path(td)
        return create_app(db_path=root / "app.sqlite3", data_dir=root / "data")

    def test_core_messaging_and_pricing_share_one_database_instance(self):
        with tempfile.TemporaryDirectory() as td, patch(
            "ladies_v9.messaging.service.WhatsAppGateway", NoNodeGateway
        ):
            app = self.make_app(td)

            database = app.extensions["ladies_database"]
            repositories = app.extensions["ladies_repositories"]
            messaging = app.extensions["ladies_messaging"]
            pricing = app.extensions["ladies_pricing"]

            self.assertIs(repositories.search.database, database)
            self.assertIs(repositories.persons.database, database)
            self.assertIs(repositories.history.database, database)
            self.assertIs(repositories.settings.database, database)
            self.assertIs(repositories.archive.database, database)
            self.assertIs(messaging.database, database)
            self.assertIs(pricing.database, database)
            self.assertEqual(database.schema_version(), 1)

    def test_security_headers_are_applied_centrally(self):
        with tempfile.TemporaryDirectory() as td, patch(
            "ladies_v9.messaging.service.WhatsAppGateway", NoNodeGateway
        ):
            response = self.make_app(td).test_client().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.headers.get("Referrer-Policy"), "same-origin")
        self.assertEqual(response.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertIn("frame-ancestors 'self'", response.headers.get("Content-Security-Policy", ""))
        self.assertTrue(response.headers.get("X-Request-ID"))

    def test_unexpected_api_error_does_not_expose_internal_details(self):
        secret = "database password=/private/secret.db token=very-secret"
        with tempfile.TemporaryDirectory() as td, patch(
            "ladies_v9.messaging.service.WhatsAppGateway", NoNodeGateway
        ):
            app = self.make_app(td)

            @app.get("/api/_test-unexpected")
            def fail_for_contract_test():
                raise RuntimeError(secret)

            response = app.test_client().get("/api/_test-unexpected")

        self.assertEqual(response.status_code, 500)
        payload = response.get_json()
        self.assertEqual(payload["error"], "Interner Serverfehler")
        self.assertEqual(payload["code"], "INTERNAL_ERROR")
        self.assertTrue(payload["request_id"])
        body = response.get_data(as_text=True)
        self.assertNotIn(secret, body)
        self.assertNotIn("very-secret", body)


if __name__ == "__main__":
    unittest.main()
