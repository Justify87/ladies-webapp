from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from ladies_v9.models import IdentityEvent, Listing, Person, PersonIdentifier, PersonUserState
from ladies_v9.messaging.integration import reconcile_identity_events
from ladies_v9.messaging.models import ChatMessage, ChatThread, ContactEndpoint


class FakeWorker:
    def __init__(self, worker_dir, auth_dir, event_handler):
        self.event_handler = event_handler
        self.state = "CONNECTED"
        self.detail = None
        self.qr = None
        self.account = "491701234567:1@s.whatsapp.net"
        self.last_connected_at = 1
        self.fail_send = False
        self.commands = []

    def start(self):
        return None

    def stop(self):
        return None

    def status(self):
        return {
            "state": self.state,
            "detail": self.detail,
            "qr_available": bool(self.qr),
            "account": self.account,
            "last_connected_at": self.last_connected_at,
        }

    def command(self, command_type, timeout=15, **payload):
        self.commands.append((command_type, payload))
        if command_type == "send_text":
            if self.fail_send:
                raise RuntimeError("send failed")
            return {"ok": True, "payload": {"provider_message_id": "wa-out-1"}}
        return {"ok": True}


class MessagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = patch("ladies_v9.messaging.service.BaileysWorkerManager", FakeWorker)
        self.addCleanup(patcher.stop)
        patcher.start()
        from ladies_v9.messaging.service import MessagingService
        self.service = MessagingService(self.root / "test.sqlite3", self.root / "data")

    def tearDown(self):
        self.tmp.cleanup()

    def make_person(self, name="Vanessa", phone=None):
        with self.service.database.session() as db:
            person = Person(display_name=name)
            db.add(person)
            db.flush()
            db.add(PersonUserState(person_id=person.person_id))
            if phone:
                db.add(PersonIdentifier(person_id=person.person_id, kind="PHONE", normalized_value=phone, raw_value=phone))
            return person.person_id

    def test_known_number_maps_to_existing_person(self):
        person_id = self.make_person(phone="+4917611111111")
        self.service._ingest_message({
            "provider_message_id": "wa-in-1",
            "jid": "4917611111111@s.whatsapp.net",
            "from_me": False,
            "text": "Hi",
            "message_type": "TEXT",
        })
        with self.service.database.session() as db:
            endpoint = db.scalar(select(ContactEndpoint).where(ContactEndpoint.normalized_phone == "+4917611111111"))
            thread = db.scalar(select(ChatThread).where(ChatThread.person_id == person_id))
            self.assertEqual(endpoint.person_id, person_id)
            self.assertIsNotNone(thread)
            self.assertEqual(thread.unread_count, 1)

    def test_two_numbers_share_one_person_thread(self):
        person_id = self.make_person(phone="+4917611111111")
        with self.service.database.session() as db:
            db.add(PersonIdentifier(person_id=person_id, kind="PHONE", normalized_value="+4917622222222", raw_value="+49 176 22222222"))
        self.service.sync_person(person_id)
        summary = self.service.ensure_person_thread(person_id)
        with self.service.database.session() as db:
            endpoints = db.scalars(select(ContactEndpoint).where(ContactEndpoint.person_id == person_id)).all()
            threads = db.scalars(select(ChatThread).where(ChatThread.person_id == person_id)).all()
            self.assertEqual(len(endpoints), 2)
            self.assertEqual(len(threads), 1)
            self.assertEqual(summary["thread_id"], threads[0].thread_id)

    def test_unknown_number_creates_unassigned_thread(self):
        self.service._ingest_message({
            "provider_message_id": "wa-unknown-1",
            "jid": "4917799999999@s.whatsapp.net",
            "from_me": False,
            "text": "Hallo",
            "message_type": "TEXT",
        })
        items = self.service.unassigned()
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["unassigned"])

    def test_assign_unassigned_merges_into_existing_person_thread(self):
        person_id = self.make_person(phone="+4917611111111")
        existing = self.service.ensure_person_thread(person_id)["thread_id"]
        self.service._ingest_message({
            "provider_message_id": "wa-unknown-2",
            "jid": "4917799999999@s.whatsapp.net",
            "from_me": False,
            "text": "Neue Nummer",
            "message_type": "TEXT",
        })
        unknown = self.service.unassigned()[0]
        endpoint_id = unknown["endpoint"]["endpoint_id"]
        self.service.assign_endpoint(endpoint_id, person_id)
        with self.service.database.session() as db:
            threads = db.scalars(select(ChatThread).where(ChatThread.person_id == person_id)).all()
            count = db.scalar(select(func.count(ChatMessage.message_id)).where(ChatMessage.thread_id == existing))
            self.assertEqual(len(threads), 1)
            self.assertGreaterEqual(count, 2)  # incoming + local assignment system message

    def test_duplicate_provider_message_is_idempotent(self):
        payload = {
            "provider_message_id": "same-id",
            "jid": "4917799999999@s.whatsapp.net",
            "from_me": False,
            "text": "Einmal",
            "message_type": "TEXT",
        }
        self.service._ingest_message(payload)
        self.service._ingest_message(payload)
        with self.service.database.session() as db:
            count = db.scalar(select(func.count(ChatMessage.message_id)).where(ChatMessage.provider_message_id == "same-id"))
            self.assertEqual(count, 1)

    def test_outgoing_pending_becomes_sent(self):
        person_id = self.make_person(phone="+4917611111111")
        thread_id = self.service.ensure_person_thread(person_id)["thread_id"]
        result = self.service.send_text(thread_id, "Hallo")
        self.assertEqual(result["status"], "SENT")
        self.assertEqual(result["provider_message_id"], "wa-out-1")

    def test_outgoing_pending_becomes_failed(self):
        person_id = self.make_person(phone="+4917611111111")
        thread_id = self.service.ensure_person_thread(person_id)["thread_id"]
        self.service.worker.fail_send = True
        with self.assertRaises(RuntimeError):
            self.service.send_text(thread_id, "Hallo")
        with self.service.database.session() as db:
            message = db.scalar(select(ChatMessage).where(ChatMessage.thread_id == thread_id, ChatMessage.direction == "OUT"))
            self.assertEqual(message.status, "FAILED")

    def test_person_merge_moves_endpoints_and_threads(self):
        source = self.make_person("Quelle", "+4917611111111")
        target = self.make_person("Ziel", "+4917622222222")
        source_thread = self.service.ensure_person_thread(source)["thread_id"]
        target_thread = self.service.ensure_person_thread(target)["thread_id"]
        with self.service.database.session() as db:
            listing = Listing(person_id=source, source="LADIES", external_listing_id="123", current_status="ACTIVE", match_decision="MATCH")
            db.add(listing)
            db.flush()
            db.add(ChatMessage(thread_id=source_thread, endpoint_id=None, direction="SYSTEM", message_type="SYSTEM", text="alt", status="RECEIVED"))
            db.add(IdentityEvent(listing_id=listing.listing_id, previous_person_id=source, new_person_id=target, action="MANUAL_MERGE"))
            # Identity normally deletes source after moving listings; mimic final canonical state.
            listing.person_id = target
            db.delete(db.get(PersonUserState, source))
            db.delete(db.get(Person, source))
        reconcile_identity_events(self.service.database)
        with self.service.database.session() as db:
            self.assertIsNone(db.scalar(select(ChatThread).where(ChatThread.person_id == source)))
            target_threads = db.scalars(select(ChatThread).where(ChatThread.person_id == target)).all()
            self.assertEqual(len(target_threads), 1)
            self.assertEqual(target_threads[0].thread_id, target_thread)
            moved = db.scalar(select(ChatMessage).where(ChatMessage.thread_id == target_thread, ChatMessage.text == "alt"))
            self.assertIsNotNone(moved)

    def test_logout_does_not_delete_local_history(self):
        person_id = self.make_person(phone="+4917611111111")
        thread_id = self.service.ensure_person_thread(person_id)["thread_id"]
        with self.service.database.session() as db:
            db.add(ChatMessage(thread_id=thread_id, endpoint_id=None, direction="SYSTEM", message_type="SYSTEM", text="bleibt", status="RECEIVED"))
        self.service.logout()
        with self.service.database.session() as db:
            self.assertEqual(db.scalar(select(func.count(ChatMessage.message_id)).where(ChatMessage.thread_id == thread_id)), 1)


if __name__ == "__main__":
    unittest.main()
