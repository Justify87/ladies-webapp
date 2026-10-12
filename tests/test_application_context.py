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
    def test_core_messaging_and_pricing_share_one_database_instance(self):
        from ladies_v9.app import create_app

        with tempfile.TemporaryDirectory() as td, patch(
            "ladies_v9.messaging.service.WhatsAppGateway", NoNodeGateway
        ):
            root = Path(td)
            app = create_app(db_path=root / "app.sqlite3", data_dir=root / "data")

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


if __name__ == "__main__":
    unittest.main()
