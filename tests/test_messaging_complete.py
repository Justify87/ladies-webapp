from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from ladies_v9.models import Person, PersonIdentifier, PersonUserState
from ladies_v9.messaging.models import ChatMessage, ChatMessageMeta, ChatReaction


class FeatureWorker:
    def __init__(self, worker_dir, auth_dir, event_handler):
        self.event_handler = event_handler
        self.auth_dir = Path(auth_dir)
        self.state = "NOT_CONFIGURED"
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
            provider_id = f"out-{self.counter}"
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


class CompleteMessagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = patch("ladies_v9.messaging.service_v92.BaileysWorkerManager", FeatureWorker)
        self.addCleanup(patcher.stop)
        patcher.start()
        from ladies_v9.messaging import MessagingService
        self.service = MessagingService(self.root / "app.sqlite3", self.root / "data")
        self.person_id = self._person("Nina", "+4917612345678")
        self.thread_id = self.service.ensure_person_thread(self.person_id)["thread_id"]
        self.endpoint_id = self.service.ensure_person_thread(self.person_id)["endpoint"]["endpoint_id"]

    def tearDown(self):
        self.tmp.cleanup()

    def _person(self, name, phone):
        with self.service.database.session() as db:
            person = Person(display_name=name)
            db.add(person)
            db.flush()
            db.add(PersonUserState(person_id=person.person_id))
            db.add(PersonIdentifier(
                person_id=person.person_id,
                kind="PHONE",
                normalized_value=phone,
                raw_value=phone,
            ))
            return person.person_id

    def _incoming(self, provider_id="in-1", text="Hallo", **extra):
        payload = {
            "provider_message_id": provider_id,
            "provider_key": {
                "remoteJid": "4917612345678@s.whatsapp.net",
                "id": provider_id,
                "fromMe": False,
            },
            "jid": "4917612345678@s.whatsapp.net",
            "phone_jid": "4917612345678@s.whatsapp.net",
            "from_me": False,
            "text": text,
            "message_type": "TEXT",
            **extra,
        }
        self.service._ingest_message(payload)
        with self.service.database.session() as db:
            return db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == provider_id)).message_id

    def test_open_chat_read_uses_real_provider_keys(self):
        message_id = self._incoming()
        self.service.worker.state = "CONNECTED"
        result = self.service.mark_read(self.thread_id)
        self.assertEqual(result["provider_receipts"], 1)
        command, payload = self.service.worker.commands[-1]
        self.assertEqual(command, "mark_read")
        self.assertEqual(payload["keys"][0]["id"], "in-1")
        with self.service.database.session() as db:
            meta = db.get(ChatMessageMeta, message_id)
            self.assertIsNotNone(meta.read_by_me_at)
            self.assertIsNotNone(meta.read_receipt_sent_at)

    def test_read_receipt_is_deferred_and_flushed_after_reconnect(self):
        message_id = self._incoming("in-deferred")
        self.service.worker.state = "DISCONNECTED"
        self.service.mark_read(self.thread_id)
        with self.service.database.session() as db:
            self.assertIsNone(db.get(ChatMessageMeta, message_id).read_receipt_sent_at)
        self.service.worker.state = "CONNECTED"
        self.assertEqual(self.service.flush_pending_read_receipts(), 1)
        self.assertEqual(self.service.worker.commands[-1][0], "mark_read")

    def test_reply_is_sent_as_baileys_quote(self):
        incoming_id = self._incoming("quoted-in", "Original")
        result = self.service.send_text(self.thread_id, "Antwort", reply_to=incoming_id)
        self.assertEqual(result["status"], "SENT")
        command, payload = next(x for x in reversed(self.service.worker.commands) if x[0] == "send_text")
        self.assertEqual(command, "send_text")
        self.assertEqual(payload["quote"]["key"]["id"], "quoted-in")
        self.assertEqual(payload["quote"]["text"], "Original")

    def test_reaction_add_change_remove(self):
        message_id = self._incoming("reaction-target")
        self.service.send_reaction(message_id, "❤️")
        with self.service.database.session() as db:
            reaction = db.scalar(select(ChatReaction).where(ChatReaction.message_id == message_id))
            self.assertEqual(reaction.emoji, "❤️")
        self.service.send_reaction(message_id, "👍")
        self.service.send_reaction(message_id, "")
        with self.service.database.session() as db:
            self.assertIsNone(db.scalar(select(ChatReaction).where(ChatReaction.message_id == message_id)))
        self.assertEqual(self.service.worker.commands[-1][0], "send_reaction")
        self.assertEqual(self.service.worker.commands[-1][1]["emoji"], "")

    def test_media_send_supports_view_once_and_retry_metadata(self):
        media = self.service.media_dir / "sample.jpg"
        media.write_bytes(b"jpeg-placeholder")
        result = self.service.send_media(
            self.thread_id,
            archive_name=media.name,
            media_type="IMAGE",
            mime_type="image/jpeg",
            caption="Foto",
            endpoint_id=self.endpoint_id,
            view_once=True,
        )
        self.assertTrue(result["view_once"])
        command, payload = next(x for x in reversed(self.service.worker.commands) if x[0] == "send_media")
        self.assertEqual(command, "send_media")
        self.assertTrue(payload["view_once"])
        self.assertEqual(payload["media_type"], "IMAGE")

    def test_view_once_incoming_can_only_be_revealed_once(self):
        # Legacy V9.3-preview view-once files may still live in the normal media
        # directory. The hardened path must consume them once, return a private
        # file handle, and delete the original file immediately afterwards.
        media = self.service.media_dir / "once.jpg"
        media.write_bytes(b"once")
        message_id = self._incoming(
            "view-once-in",
            text=None,
            message_type="IMAGE",
            media={"archive_name": media.name, "mime_type": "image/jpeg", "local_url": "/archive/whatsapp/media/once.jpg"},
            view_once=True,
        )
        handle, mime = self.service.view_once_path(message_id)
        try:
            self.assertEqual(handle.read(), b"once")
        finally:
            handle.close()
        self.assertEqual(mime, "image/jpeg")
        self.assertFalse(media.exists())
        with self.assertRaises(RuntimeError):
            self.service.view_once_path(message_id)
        payload = self.service.message(message_id)
        self.assertTrue(payload["view_once_consumed"])
        self.assertNotIn("local_url", payload.get("media") or {})
        self.assertNotIn("archive_name", payload.get("media") or {})

    def test_edit_and_delete_own_text_message(self):
        sent = self.service.send_text(self.thread_id, "Alt")
        edited = self.service.edit_message(sent["message_id"], "Neu")
        self.assertEqual(edited["text"], "Neu")
        self.assertIsNotNone(edited["edited_at"])
        deleted = self.service.delete_message(sent["message_id"])
        self.assertEqual(deleted["status"], "DELETED")
        self.assertIsNotNone(deleted["deleted_at"])
        self.assertIsNone(deleted["text"])

    def test_played_receipt_is_persisted_for_voice_status(self):
        sent = self.service.send_text(self.thread_id, "Status")
        self.service._worker_event({
            "type": "message_update",
            "payload": {"provider_message_id": sent["provider_message_id"], "status": "PLAYED"},
        })
        self.assertEqual(self.service.message(sent["message_id"])["status"], "PLAYED")


if __name__ == "__main__":
    unittest.main()
