from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ladies_v9.lan_access import LanAccessManager
from ladies_v9.models import Person, PersonIdentifier, PersonUserState


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


class FakeUpstream:
    def __init__(self, chunks, headers):
        self._chunks = list(chunks)
        self.headers = dict(headers)
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=128 * 1024):
        del chunk_size
        yield from self._chunks

    def close(self):
        self.closed = True


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

    def test_voice_conversion_failure_is_sanitized_and_upload_is_removed(self):
        with tempfile.TemporaryDirectory() as td, patch(
            "ladies_v9.messaging.service.WhatsAppGateway", NoNodeGateway
        ):
            app = self.make_app(td)
            service = app.extensions["ladies_messaging"]
            with service.database.session() as db:
                person = Person(display_name="Voice")
                db.add(person)
                db.flush()
                db.add(PersonUserState(person_id=person.person_id))
                db.add(PersonIdentifier(
                    person_id=person.person_id,
                    kind="PHONE",
                    normalized_value="+4917612345678",
                    raw_value="+49 176 12345678",
                ))
                person_id = person.person_id
            ensured = service.ensure_person_thread(person_id)
            before = set(service.media_dir.iterdir())
            private_detail = "/home/private-user/recording.webm: invalid input"

            with patch(
                "ladies_v9.messaging.media_store.imageio_ffmpeg.get_ffmpeg_exe",
                return_value="ffmpeg",
            ), patch(
                "ladies_v9.messaging.media_store.subprocess.run",
                return_value=SimpleNamespace(returncode=1, stderr=private_detail),
            ):
                response = app.test_client().post(
                    f"/api/messaging/threads/{ensured['thread_id']}/media",
                    data={
                        "media_type": "VOICE",
                        "endpoint_id": str(ensured["endpoint"]["endpoint_id"]),
                        "file": (io.BytesIO(b"webm-placeholder"), "recording.webm"),
                    },
                    content_type="multipart/form-data",
                    environ_base={"REMOTE_ADDR": "127.0.0.1"},
                )
            after = set(service.media_dir.iterdir())

        self.assertEqual(response.status_code, 400)
        body = response.get_data(as_text=True)
        self.assertIn("Sprachnachricht konnte nicht verarbeitet werden", body)
        self.assertNotIn(private_detail, body)
        self.assertNotIn("/home/private-user", body)
        self.assertEqual(after, before)

    def test_media_proxy_checks_complete_body_before_success(self):
        cases = [
            (
                "declared-too-large",
                {"Content-Type": "image/jpeg", "Content-Length": "9"},
                [b"ignored"],
                413,
                None,
            ),
            (
                "unknown-length-too-large",
                {"Content-Type": "image/jpeg"},
                [b"1234", b"56789"],
                413,
                None,
            ),
            (
                "unknown-length-valid",
                {"Content-Type": "image/jpeg"},
                [b"1234", b"56"],
                200,
                b"123456",
            ),
            (
                "wrong-small-length",
                {"Content-Type": "video/mp4", "Content-Length": "4"},
                [b"1234", b"56789"],
                413,
                None,
            ),
            (
                "unsupported-mime",
                {"Content-Type": "text/plain"},
                [b"plain"],
                400,
                None,
            ),
        ]
        with tempfile.TemporaryDirectory() as td, patch(
            "ladies_v9.messaging.service.WhatsAppGateway", NoNodeGateway
        ):
            app = self.make_app(td)
            client = app.test_client()
            http = app.extensions["ladies_http"]
            for name, headers, chunks, expected_status, expected_body in cases:
                upstream = FakeUpstream(chunks, headers)
                with self.subTest(name=name), patch.object(
                    http, "get", return_value=upstream
                ), patch("ladies_v9.web.MEDIA_PROXY_LIMIT", 8):
                    response = client.get(
                        "/api/media-proxy",
                        query_string={"url": "https://bilder1.ladies-cdn.de/test.jpg"},
                        environ_base={"REMOTE_ADDR": "127.0.0.1"},
                    )
                    try:
                        body = response.get_data()
                        self.assertEqual(response.status_code, expected_status)
                        self.assertTrue(upstream.closed)
                        if expected_status == 200:
                            self.assertEqual(body, expected_body)
                            self.assertEqual(response.headers.get("Content-Length"), str(len(expected_body)))
                            self.assertEqual(response.headers.get("Cache-Control"), "private, max-age=3600")
                        else:
                            self.assertNotIn("Cache-Control", response.headers)
                            self.assertNotIn(b"123456789", body)
                    finally:
                        response.close()

    def test_release_workflow_is_immutable_and_requires_synchronized_versions(self):
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("tags:", release)
        self.assertIn("'v*.*.*'", release)
        self.assertIn("Release version mismatch", release)
        self.assertIn("package.json", release)
        self.assertIn("package-lock.json", release)
        self.assertIn(".github/release-notes", release)
        for forbidden in (
            "git push",
            "git commit",
            "npm --prefix ladies/messaging/whatsapp_worker version",
            "uv lock",
            "--allow-same-version",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, release)

    def test_release_publishes_verified_artifact_and_platform_ci_is_separate(self):
        workflows = ROOT / ".github" / "workflows"
        release = (workflows / "release.yml").read_text(encoding="utf-8")
        ci = (workflows / "ci.yml").read_text(encoding="utf-8")
        platform = (workflows / "platform.yml").read_text(encoding="utf-8")

        self.assertFalse((workflows / "checks.yml").exists())
        self.assertEqual(release.count("tools/build_release.py"), 1)
        self.assertIn('--commit "${GITHUB_SHA}"', release)
        self.assertIn("actions/upload-artifact@", release)
        self.assertIn("actions/download-artifact@", release)
        self.assertIn("--verify-tag", release)
        self.assertIn('--target "$GITHUB_SHA"', release)
        self.assertIn("! -name 'test_portable_*.py'", ci)
        self.assertIn("test_portable_*.py", platform)
        self.assertIn("windows-2025", platform)
        self.assertIn("macos-15", platform)
        self.assertIn("cancel-in-progress: false", ci)
        self.assertIn("cancel-in-progress: false", platform)

    def test_source_version_fallback_comes_from_pyproject(self):
        import tomllib
        from ladies_v9.config import _source_project_version

        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(_source_project_version(), data["project"]["version"])


if __name__ == "__main__":
    unittest.main()
