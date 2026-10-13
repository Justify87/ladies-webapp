from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ladies_v9.lan_access import LanAccessManager


ROOT = Path(__file__).resolve().parents[1]


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


class ReviewHardeningTests(unittest.TestCase):
    def make_app(self, td):
        from ladies_v9.app import create_app

        root = Path(td)
        return create_app(db_path=root / "app.sqlite3", data_dir=root / "data")

    def test_lan_session_activity_is_persisted_when_stale(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = LanAccessManager(root)
            session = first.pair(first.pairing_token)
            self.assertIsNotNone(session)

            state_path = root / "lan_access.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            digest = next(iter(state["sessions"]))
            state["sessions"][digest]["last_seen_at"] = "2000-01-01T00:00:00+00:00"
            state_path.write_text(json.dumps(state), encoding="utf-8")

            second = LanAccessManager(root)
            self.assertTrue(second.validate_session(session))
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertNotEqual(
                persisted["sessions"][digest]["last_seen_at"],
                "2000-01-01T00:00:00+00:00",
            )

    def test_oversized_api_request_uses_json_error_contract(self):
        with tempfile.TemporaryDirectory() as td, patch(
            "ladies_v9.messaging.service.WhatsAppGateway", NoNodeGateway
        ):
            app = self.make_app(td)
            app.config["MAX_CONTENT_LENGTH"] = 64
            response = app.test_client().post(
                "/api/client-log",
                data=json.dumps({"message": "x" * 256}),
                content_type="application/json",
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )

        self.assertEqual(response.status_code, 413)
        payload = response.get_json()
        self.assertEqual(payload["code"], "PAYLOAD_TOO_LARGE")
        self.assertEqual(payload["error"], "Anfrage ist zu groß")
        self.assertTrue(payload["request_id"])

    def test_invalid_view_once_document_is_rejected_before_file_is_saved(self):
        with tempfile.TemporaryDirectory() as td, patch(
            "ladies_v9.messaging.service.WhatsAppGateway", NoNodeGateway
        ):
            app = self.make_app(td)
            service = app.extensions["ladies_messaging"]
            media_dir = service.media_dir
            before = set(media_dir.iterdir())
            response = app.test_client().post(
                "/api/messaging/threads/999/media",
                data={
                    "media_type": "DOCUMENT",
                    "view_once": "true",
                    "file": (io.BytesIO(b"document"), "note.txt"),
                },
                content_type="multipart/form-data",
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
            after = set(media_dir.iterdir())

        self.assertEqual(response.status_code, 400)
        self.assertIn("Einmal ansehen", response.get_json()["error"])
        self.assertEqual(after, before)

    def test_release_workflow_allows_same_worker_version(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("--allow-same-version", workflow)

    def test_source_version_fallback_comes_from_pyproject(self):
        import tomllib
        from ladies_v9.config import _source_project_version

        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(_source_project_version(), data["project"]["version"])


if __name__ == "__main__":
    unittest.main()
