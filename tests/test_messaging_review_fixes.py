from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select

from ladies_v9.models import Person, PersonIdentifier, PersonUserState
from ladies_v9.messaging.models import ChatMessage, ChatMessageMeta, ContactEndpoint


class ReviewWorker:
    def __init__(self, worker_dir, auth_dir, event_handler):
        self.event_handler = event_handler
        self.auth_dir = Path(auth_dir)
        self.state = "CONNECTED"
        self.detail = None
        self.qr = None
        self.account = None
        self.last_connected_at = None
        self.proc = None
        self.commands = []
        self.counter = 0

    def dependency_status(self):
        return True, None

    def start(self):
        self.state = "CONNECTED"

    def stop(self):
        pass

    def status(self):
        return {
            "state": self.state,
            "detail": self.detail,
            "qr_available": False,
            "account": self.account,
            "last_connected_at": self.last_connected_at,
        }

    def command(self, command_type, timeout=15, **payload):
        self.commands.append((command_type, payload))
        if command_type in {"send_text", "send_media"}:
            self.counter += 1
            provider_id = f"out-review-{self.counter}"
            return {
                "ok": True,
                "payload": {
                    "provider_message_id": provider_id,
                    "provider_key": {
                        "remoteJid": payload["jid"],
                        "id": provider_id,
                        "fromMe": True,
                    },
                },
            }
        return {"ok": True, "payload": {}}


class MessagingReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = patch("ladies_v9.messaging.service_v92.BaileysWorkerManager", ReviewWorker)
        self.addCleanup(patcher.stop)
        patcher.start()
        from ladies_v9.messaging import MessagingService
        self.service = MessagingService(self.root / "app.sqlite3", self.root / "data")
        with self.service.database.session() as db:
            person = Person(display_name="Review")
            db.add(person)
            db.flush()
            self.person_id = person.person_id
            db.add(PersonUserState(person_id=person.person_id))
            db.add(PersonIdentifier(
                person_id=person.person_id,
                kind="PHONE",
                normalized_value="+4917611111111",
                raw_value="+4917611111111",
            ))
        ensured = self.service.ensure_person_thread(self.person_id)
        self.thread_id = ensured["thread_id"]
        self.endpoint_id = ensured["endpoint"]["endpoint_id"]

    def tearDown(self):
        self.tmp.cleanup()

    def _incoming(self, provider_id="incoming-review", text="Hallo", endpoint_id=None, **extra):
        endpoint_id = endpoint_id or self.endpoint_id
        with self.service.database.session() as db:
            endpoint = db.get(ContactEndpoint, endpoint_id)
            jid = endpoint.provider_address or endpoint.normalized_phone.lstrip("+") + "@s.whatsapp.net"
        payload = {
            "provider_message_id": provider_id,
            "provider_key": {"remoteJid": jid, "id": provider_id, "fromMe": False},
            "jid": jid,
            "phone_jid": jid if jid.endswith("@s.whatsapp.net") else None,
            "from_me": False,
            "text": text,
            "message_type": "TEXT",
            **extra,
        }
        self.service._ingest_message(payload)
        with self.service.database.session() as db:
            return db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == provider_id)).message_id

    def test_reply_cannot_cross_persons_whatsapp_numbers(self):
        original_id = self._incoming("reply-old-number")
        with self.service.database.session() as db:
            second = ContactEndpoint(
                person_id=self.person_id,
                normalized_phone="+4917622222222",
                display_phone="+4917622222222",
                provider_address="4917622222222@s.whatsapp.net",
                state="ACTIVE",
            )
            db.add(second)
            db.flush()
            second_id = second.endpoint_id
        with self.assertRaisesRegex(ValueError, "derselben WhatsApp-Nummer"):
            self.service.send_text(self.thread_id, "falsch geroutete Antwort", second_id, original_id)

    def test_remote_edit_and_delete_are_applied(self):
        message_id = self._incoming("remote-change", "Alt")
        self.service._worker_event({
            "type": "message_content_update",
            "payload": {"provider_message_id": "remote-change", "action": "EDIT", "text": "Neu"},
        })
        edited = self.service.message(message_id)
        self.assertEqual(edited["text"], "Neu")
        self.assertIsNotNone(edited["edited_at"])

        self.service._worker_event({
            "type": "message_content_update",
            "payload": {"provider_message_id": "remote-change", "action": "DELETE"},
        })
        deleted = self.service.message(message_id)
        self.assertEqual(deleted["status"], "DELETED")
        self.assertIsNone(deleted["text"])
        self.assertIsNotNone(deleted["deleted_at"])

    def test_view_once_is_private_consumed_atomically_and_deleted(self):
        private = self.service.view_once_dir / "once.jpg"
        private.write_bytes(b"private-once")
        message_id = self._incoming(
            "private-once",
            text=None,
            message_type="IMAGE",
            media={
                "archive_name": private.name,
                "private_view_once": True,
                "mime_type": "image/jpeg",
            },
            view_once=True,
        )
        payload = self.service.message(message_id)
        self.assertNotIn("archive_name", payload.get("media") or {})
        self.assertNotIn("local_url", payload.get("media") or {})

        handle, mime = self.service.view_once_path(message_id)
        try:
            self.assertEqual(mime, "image/jpeg")
            self.assertEqual(handle.read(), b"private-once")
        finally:
            handle.close()
        self.assertFalse(private.exists())
        with self.assertRaises(RuntimeError):
            self.service.view_once_path(message_id)

    def test_legacy_media_url_is_rewritten_to_guarded_message_endpoint(self):
        legacy = self.service.media_dir / "legacy.jpg"
        legacy.write_bytes(b"legacy")
        message_id = self._incoming(
            "legacy-media",
            text=None,
            message_type="IMAGE",
            media={"local_url": "/archive/whatsapp/media/legacy.jpg", "mime_type": "image/jpeg"},
        )
        payload = self.service.message(message_id)
        self.assertEqual(payload["media"]["local_url"], f"/api/messaging/messages/{message_id}/media")
        path, mime, _name, attachment = self.service.media_path(message_id)
        self.assertEqual(path, legacy.resolve())
        self.assertEqual(mime, "image/jpeg")
        self.assertFalse(attachment)

    def test_document_active_content_is_forced_to_attachment(self):
        document = self.service.media_dir / "danger.html"
        document.write_text("<script>fetch('/api/dashboard')</script>", encoding="utf-8")
        with self.service.database.session() as db:
            message = ChatMessage(
                thread_id=self.thread_id,
                endpoint_id=self.endpoint_id,
                direction="IN",
                message_type="DOCUMENT",
                text=None,
                media_json=json.dumps({
                    "archive_name": document.name,
                    "mime_type": "text/html",
                    "file_name": "danger.html",
                }),
                status="RECEIVED",
            )
            db.add(message)
            db.flush()
            message_id = message.message_id
        path, mime, name, attachment = self.service.media_path(message_id)
        self.assertEqual(path, document.resolve())
        self.assertEqual(mime, "application/octet-stream")
        self.assertEqual(name, "danger.html")
        self.assertTrue(attachment)

    def test_chromium_webm_voice_is_normalized_to_ogg_opus(self):
        source = self.service.media_dir / "recording.webm"
        source.write_bytes(b"webm-placeholder")

        def fake_run(command, **_kwargs):
            self.assertIn("libopus", command)
            self.assertEqual(command[-2], "ogg")
            Path(command[-1]).write_bytes(b"OggS")
            return SimpleNamespace(returncode=0, stderr="")

        with patch("ladies_v9.messaging.service_reviewfix.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"), patch(
            "ladies_v9.messaging.service_reviewfix.subprocess.run", side_effect=fake_run
        ):
            result = self.service.send_media(
                self.thread_id,
                archive_name=source.name,
                media_type="VOICE",
                mime_type="audio/webm;codecs=opus",
                file_name="recording.webm",
                endpoint_id=self.endpoint_id,
            )
        self.assertEqual(result["message_type"], "VOICE")
        command, payload = next(x for x in reversed(self.service.worker.commands) if x[0] == "send_media")
        self.assertEqual(command, "send_media")
        self.assertTrue(payload["file_path"].endswith(".ogg"))
        self.assertEqual(payload["mime_type"], "audio/ogg; codecs=opus")
        self.assertFalse(source.exists())


class MediaHttpHardeningTests(unittest.TestCase):
    def test_archive_bypass_is_blocked_and_html_document_is_attachment(self):
        with tempfile.TemporaryDirectory() as td, patch(
            "ladies_v9.messaging.service_v92.BaileysWorkerManager", ReviewWorker
        ):
            from ladies_v9.app import create_app
            root = Path(td)
            app = create_app(db_path=root / "app.sqlite3", data_dir=root / "data")
            service = app.extensions["ladies_messaging"]
            with service.database.session() as db:
                person = Person(display_name="HTTP")
                db.add(person)
                db.flush()
                db.add(PersonUserState(person_id=person.person_id))
                endpoint = ContactEndpoint(
                    person_id=person.person_id,
                    normalized_phone="+4917633333333",
                    provider_address="4917633333333@s.whatsapp.net",
                    state="ACTIVE",
                )
                db.add(endpoint)
                db.flush()
                thread = service._get_thread(db, person_id=person.person_id)
                document = service.media_dir / "active.html"
                document.write_text("<script>top.location='/api/dashboard'</script>", encoding="utf-8")
                message = ChatMessage(
                    thread_id=thread.thread_id,
                    endpoint_id=endpoint.endpoint_id,
                    direction="IN",
                    message_type="DOCUMENT",
                    media_json=json.dumps({
                        "archive_name": document.name,
                        "mime_type": "text/html",
                        "file_name": "active.html",
                    }),
                    status="RECEIVED",
                )
                db.add(message)
                db.flush()
                message_id = message.message_id

            client = app.test_client()
            blocked = client.get("/archive/whatsapp/media/active.html", environ_base={"REMOTE_ADDR": "127.0.0.1"})
            self.assertEqual(blocked.status_code, 404)

            response = client.get(
                f"/api/messaging/messages/{message_id}/media",
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers["Content-Type"].startswith("application/octet-stream"))
            self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
            self.assertIn("sandbox", response.headers.get("Content-Security-Policy", ""))
            self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")


if __name__ == "__main__":
    unittest.main()
