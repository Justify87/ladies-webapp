from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ladies_v9.database import Database
from unittest.mock import patch

from sqlalchemy import func, select

from ladies_v9.models import Person, PersonIdentifier, PersonUserState
from ladies_v9.messaging.history_cache import REHYDRATE_LIMIT
from ladies_v9.messaging.models import ChatMessage, ChatThread, ContactEndpoint


PHONE = "+4917612345678"
JID = "4917612345678@s.whatsapp.net"


def _database(path):
    database = Database(path)
    database.create_schema()
    return database


class FakeWorker:
    instances = []

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
        type(self).instances.append(self)

    def dependency_status(self):
        return True, None

    def start(self):
        self.state = "CONNECTED"

    def stop(self):
        pass

    def command(self, command_type, timeout=15, **payload):
        self.commands.append((command_type, payload))
        return {"ok": True, "payload": {"request_id": "history-request"}}


def add_person(service, name="Test") -> int:
    with service.database.session() as db:
        person = Person(display_name=name)
        db.add(person)
        db.flush()
        db.add(PersonUserState(person_id=person.person_id))
        db.add(PersonIdentifier(
            person_id=person.person_id,
            kind="PHONE",
            normalized_value=PHONE,
            raw_value="+49 176 12345678",
        ))
        return int(person.person_id)


def message_payload(index: int) -> dict:
    return {
        "provider_message_id": f"old-{index}",
        "provider_key": {
            "remoteJid": JID,
            "remoteJidAlt": None,
            "participant": None,
            "participantAlt": None,
            "id": f"old-{index}",
            "fromMe": False,
        },
        "jid": JID,
        "phone_jid": JID,
        "from_me": False,
        "timestamp": 1_700_000_000 + index,
        "message_type": "TEXT",
        "text": f"Nachricht {index}",
        "media": None,
        "quote": None,
        "view_once": False,
    }


class WhatsAppRehydrateTests(unittest.TestCase):
    def setUp(self):
        FakeWorker.instances.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        patcher = patch("ladies_v9.messaging.service.WhatsAppGateway", FakeWorker)
        self.addCleanup(patcher.stop)
        patcher.start()
        from ladies_v9.messaging import MessagingService
        self.MessagingService = MessagingService

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_database_restores_only_last_ten_cached_messages(self):
        original = self.MessagingService(_database(self.root / "old.sqlite3"), self.data_dir)
        add_person(original, "Alt")
        for index in range(12):
            original._ingest_message(message_payload(index))

        cache_path = self.data_dir / "whatsapp" / "recent-history.json"
        self.assertTrue(cache_path.exists())

        fresh = self.MessagingService(_database(self.root / "fresh.sqlite3"), self.data_dir)
        person_id = add_person(fresh, "Neu")
        fresh.worker.state = "CONNECTED"
        result = fresh.rehydrate_recent_history(person_id, limit=10)

        self.assertEqual(REHYDRATE_LIMIT, 10)
        self.assertEqual(result["restored"], 10)
        self.assertEqual(result["requested"], 0)
        with fresh.database.session() as db:
            thread = db.scalar(select(ChatThread).where(ChatThread.person_id == person_id))
            self.assertIsNotNone(thread)
            self.assertEqual(thread.unread_count, 0)
            rows = db.scalars(
                select(ChatMessage)
                .where(ChatMessage.thread_id == thread.thread_id, ChatMessage.direction.in_(("IN", "OUT")))
                .order_by(ChatMessage.timestamp)
            ).all()
            self.assertEqual(len(rows), 10)
            self.assertEqual(rows[0].provider_message_id, "old-2")
            self.assertEqual(rows[-1].provider_message_id, "old-11")

    def test_partial_cache_requests_only_missing_messages_up_to_ten(self):
        original = self.MessagingService(_database(self.root / "old.sqlite3"), self.data_dir)
        add_person(original, "Alt")
        for index in range(3):
            original._ingest_message(message_payload(index))

        fresh = self.MessagingService(_database(self.root / "fresh.sqlite3"), self.data_dir)
        person_id = add_person(fresh, "Neu")
        fresh.worker.state = "CONNECTED"
        result = fresh.rehydrate_recent_history(person_id, limit=10)

        self.assertEqual(result["restored"], 3)
        self.assertEqual(result["requested"], 7)
        commands = [item for item in fresh.worker.commands if item[0] == "fetch_recent_history"]
        self.assertEqual(len(commands), 1)
        payload = commands[0][1]
        self.assertEqual(payload["count"], 7)
        self.assertEqual(payload["oldest_key"]["id"], "old-0")
        self.assertEqual(payload["oldest_timestamp_ms"], 1_700_000_000_000)
        with fresh.database.session() as db:
            count = db.scalar(select(func.count(ChatMessage.message_id)).where(ChatMessage.direction.in_(("IN", "OUT"))))
            self.assertEqual(count, 3)

    def test_normal_history_seed_is_cached_without_becoming_visible_chat(self):
        service = self.MessagingService(self.root / "seed.sqlite3", self.data_dir)
        person_id = add_person(service)
        service._worker_event({"type": "history_seed", "payloads": [message_payload(1), message_payload(2)]})

        with service.database.session() as db:
            count = db.scalar(select(func.count(ChatMessage.message_id)))
            self.assertEqual(count, 0)

        service.worker.state = "DISCONNECTED"
        result = service.rehydrate_recent_history(person_id, limit=10)
        self.assertEqual(result["restored"], 2)
        self.assertEqual(result["requested"], 0)

    def test_metadata_only_history_seed_preserves_local_media_for_rehydrate(self):
        original = self.MessagingService(self.root / "old-media.sqlite3", self.data_dir)
        person_id = add_person(original, "Alt")
        original.sync_person(person_id)
        media_file = original.media_dir / "cached-media.jpg"
        media_file.write_bytes(b"cached-media")

        live = message_payload(7)
        live.update({
            "message_type": "IMAGE",
            "text": None,
            "media": {
                "archive_name": media_file.name,
                "sha256": "live-sha256",
                "private_view_once": True,
                "mime_type": "image/jpeg",
                "byte_size": len(b"cached-media"),
                "width": 320,
            },
        })
        original._ingest_message(live)

        seed = message_payload(7)
        seed.update({
            "message_type": "IMAGE",
            "text": None,
            "media": {
                "mime_type": "image/jpeg",
                "width": 640,
                "height": 480,
            },
        })
        original._worker_event({"type": "history_seed", "payloads": [seed]})

        with original.database.session() as db:
            endpoint = db.scalar(select(ContactEndpoint).where(ContactEndpoint.person_id == person_id))
            self.assertIsNotNone(endpoint)
            cached = original.history_cache.for_endpoint(endpoint)
        row = next(item for item in cached if item["provider_message_id"] == "old-7")
        self.assertEqual(row["media"]["archive_name"], media_file.name)
        self.assertEqual(row["media"]["sha256"], "live-sha256")
        self.assertTrue(row["media"]["private_view_once"])
        self.assertEqual(row["media"]["width"], 640)
        self.assertEqual(row["media"]["height"], 480)

        fresh = self.MessagingService(self.root / "fresh-media.sqlite3", self.data_dir)
        fresh_person_id = add_person(fresh, "Neu")
        fresh.worker.state = "DISCONNECTED"
        result = fresh.rehydrate_recent_history(fresh_person_id, limit=10)
        self.assertEqual(result["restored"], 1)

        with fresh.database.session() as db:
            restored = db.scalar(select(ChatMessage).where(ChatMessage.provider_message_id == "old-7"))
            self.assertIsNotNone(restored)
            restored_id = restored.message_id
        path, mime, _name, attachment = fresh.media_path(restored_id)
        self.assertEqual(path, media_file.resolve())
        self.assertEqual(mime, "image/jpeg")
        self.assertFalse(attachment)

    def test_rehydrate_limit_is_enforced_even_when_caller_requests_more(self):
        original = self.MessagingService(_database(self.root / "old.sqlite3"), self.data_dir)
        add_person(original, "Alt")
        for index in range(20):
            original._ingest_message(message_payload(index))
        fresh = self.MessagingService(_database(self.root / "fresh.sqlite3"), self.data_dir)
        person_id = add_person(fresh, "Neu")
        result = fresh.rehydrate_recent_history(person_id, limit=100)
        self.assertEqual(result["restored"], REHYDRATE_LIMIT)


if __name__ == "__main__":
    unittest.main()
