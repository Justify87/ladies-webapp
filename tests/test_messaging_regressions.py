from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from ladies_v9.models import Person, PersonIdentifier, PersonUserState
from ladies_v9.messaging.models import ChatMessage, ChatThread, ContactEndpoint


class FakeWorker:
    def __init__(self, worker_dir, auth_dir, event_handler):
        self.event_handler = event_handler
        self.auth_dir = Path(auth_dir)
        self.state = "NOT_CONFIGURED"
        self.detail = None
        self.qr = None
        self.account = None
        self.last_connected_at = None
        self.proc = None
        self.fail_send = False
        self.receipt_before_return = False
        self.send_counter = 0

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
            "qr_available": bool(self.qr),
            "account": self.account,
            "last_connected_at": self.last_connected_at,
        }

    def command(self, command_type, timeout=15, **payload):
        if command_type == "send_text":
            if self.fail_send:
                self.fail_send = False
                raise RuntimeError("send failed")
            self.send_counter += 1
            provider_id = f"wa-out-{self.send_counter}"
            if self.receipt_before_return:
                self.event_handler({
                    "type": "message_update",
                    "payload": {
                        "provider_message_id": provider_id,
                        "client_message_id": payload.get("client_message_id"),
                        "status": "READ",
                    },
                })
            return {"ok": True, "payload": {"provider_message_id": provider_id}}
        if command_type == "disconnect":
            self.state = "DISCONNECTED"
        if command_type == "logout":
            self.state = "LOGGED_OUT"
        return {"ok": True, "payload": {}}


class MessagingRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = patch("ladies_v9.messaging.service_v92.BaileysWorkerManager", FakeWorker)
        self.addCleanup(patcher.stop)
        patcher.start()
        from ladies_v9.messaging.service_v92 import MessagingService
        self.service = MessagingService(self.root / "app.sqlite3", self.root / "data")

    def tearDown(self):
        self.tmp.cleanup()

    def make_person(self, name="Person", phone=None):
        with self.service.database.session() as db:
            person = Person(display_name=name)
            db.add(person)
            db.flush()
            db.add(PersonUserState(person_id=person.person_id))
            if phone:
                db.add(PersonIdentifier(
                    person_id=person.person_id,
                    kind="PHONE",
                    normalized_value=phone,
                    raw_value=phone,
                ))
            return person.person_id

    def test_raw_worker_events_never_reach_browser_event_hub(self):
        subscriber = self.service.events.subscribe()
        try:
            self.service._worker_event({"type": "qr", "value": "SUPER-SECRET-QR"})
            self.assertEqual(subscriber.get_nowait(), {"type": "qr_available"})

            self.service._worker_event({
                "type": "message",
                "payload": {
                    "provider_message_id": "safe-event-1",
                    "jid": "4917612345678@s.whatsapp.net",
                    "phone_jid": "4917612345678@s.whatsapp.net",
                    "from_me": False,
                    "text": "private message body",
                    "message_type": "IMAGE",
                    "media": {
                        "mime_type": "image/png",
                        "data_base64": base64.b64encode(b"not-a-real-image").decode("ascii"),
                    },
                },
            })
            event = subscriber.get_nowait()
            self.assertEqual(set(event), {"type", "thread_id", "message_id"})
            serialized = repr(event)
            self.assertNotIn("private message body", serialized)
            self.assertNotIn("4917612345678", serialized)
            self.assertNotIn("data_base64", serialized)
        finally:
            self.service.events.unsubscribe(subscriber)

    def test_lid_assigned_to_person_learns_phone_when_it_becomes_known(self):
        person_id = self.make_person("LID Person")
        self.service._ingest_message({
            "provider_message_id": "lid-first",
            "jid": "123456789012345@lid",
            "from_me": False,
            "text": "Hi",
            "message_type": "TEXT",
        })
        endpoint_id = self.service.unassigned()[0]["endpoint"]["endpoint_id"]
        self.service.assign_endpoint(endpoint_id, person_id)
        self.service._ingest_message({
            "provider_message_id": "lid-second",
            "jid": "123456789012345@lid",
            "phone_jid": "4917612345678@s.whatsapp.net",
            "from_me": False,
            "text": "Phone now visible",
            "message_type": "TEXT",
        })
        with self.service.database.session() as db:
            endpoint = db.scalar(select(ContactEndpoint).where(ContactEndpoint.provider_address == "123456789012345@lid"))
            identifier = db.scalar(select(PersonIdentifier).where(
                PersonIdentifier.person_id == person_id,
                PersonIdentifier.kind == "PHONE",
                PersonIdentifier.normalized_value == "+4917612345678",
            ))
            self.assertEqual(endpoint.person_id, person_id)
            self.assertEqual(endpoint.normalized_phone, "+4917612345678")
            self.assertEqual(endpoint.state, "ACTIVE")
            self.assertIsNotNone(identifier)

    def test_lid_phone_identity_conflict_is_not_silently_reassigned(self):
        person_a = self.make_person("A")
        person_b = self.make_person("B", "+4917612345678")
        self.service._ingest_message({
            "provider_message_id": "conflict-first",
            "jid": "998877665544332@lid",
            "from_me": False,
            "text": "Unknown",
            "message_type": "TEXT",
        })
        endpoint_id = self.service.unassigned()[0]["endpoint"]["endpoint_id"]
        self.service.assign_endpoint(endpoint_id, person_a)
        self.service._ingest_message({
            "provider_message_id": "conflict-second",
            "jid": "998877665544332@lid",
            "phone_jid": "4917612345678@s.whatsapp.net",
            "from_me": False,
            "text": "Conflict",
            "message_type": "TEXT",
        })
        with self.service.database.session() as db:
            endpoint = db.get(ContactEndpoint, endpoint_id)
            self.assertEqual(endpoint.person_id, person_a)
            self.assertIsNone(endpoint.normalized_phone)
            self.assertEqual(endpoint.state, "IDENTITY_CONFLICT")
            wrong = db.scalar(select(PersonIdentifier).where(
                PersonIdentifier.person_id == person_a,
                PersonIdentifier.normalized_value == "+4917612345678",
            ))
            right = db.scalar(select(PersonIdentifier).where(
                PersonIdentifier.person_id == person_b,
                PersonIdentifier.normalized_value == "+4917612345678",
            ))
            self.assertIsNone(wrong)
            self.assertIsNotNone(right)

    def test_existing_phone_endpoint_and_lid_endpoint_coalesce_without_unique_collision(self):
        person_id = self.make_person("Coalesce", "+4917612345678")
        self.service.sync_person(person_id)
        self.service._ingest_message({
            "provider_message_id": "coalesce-lid-1",
            "jid": "555666777888999@lid",
            "from_me": False,
            "text": "LID first",
            "message_type": "TEXT",
        })
        lid_endpoint_id = self.service.unassigned()[0]["endpoint"]["endpoint_id"]
        self.service.assign_endpoint(lid_endpoint_id, person_id)
        self.service._ingest_message({
            "provider_message_id": "coalesce-lid-2",
            "jid": "555666777888999@lid",
            "phone_jid": "4917612345678@s.whatsapp.net",
            "from_me": False,
            "text": "PN known",
            "message_type": "TEXT",
        })
        with self.service.database.session() as db:
            endpoints = db.scalars(select(ContactEndpoint).where(ContactEndpoint.person_id == person_id)).all()
            self.assertEqual(len(endpoints), 1)
            endpoint = endpoints[0]
            self.assertEqual(endpoint.normalized_phone, "+4917612345678")
            self.assertEqual(endpoint.provider_address, "555666777888999@lid")
            dangling = db.scalar(select(func.count(ChatMessage.message_id)).where(ChatMessage.endpoint_id == lid_endpoint_id))
            self.assertEqual(dangling, 0)

    def test_manual_assignment_rejects_phone_owned_by_other_person_before_move(self):
        person_a = self.make_person("A")
        person_b = self.make_person("B", "+4917612345678")
        with self.service.database.session() as db:
            endpoint = ContactEndpoint(
                person_id=None,
                provider="WHATSAPP",
                normalized_phone="+4917612345678",
                display_phone="+49 176 12345678",
                provider_address="4917612345678@s.whatsapp.net",
                state="ACTIVE",
            )
            db.add(endpoint)
            db.flush()
            thread = ChatThread(person_id=None)
            db.add(thread)
            db.flush()
            db.add(ChatMessage(
                thread_id=thread.thread_id,
                endpoint_id=endpoint.endpoint_id,
                direction="IN",
                message_type="TEXT",
                text="unknown",
                status="RECEIVED",
            ))
            endpoint_id = endpoint.endpoint_id
            thread_id = thread.thread_id
        with self.assertRaises(ValueError):
            self.service.assign_endpoint(endpoint_id, person_a)
        with self.service.database.session() as db:
            endpoint = db.get(ContactEndpoint, endpoint_id)
            message = db.scalar(select(ChatMessage).where(ChatMessage.thread_id == thread_id))
            self.assertIsNone(endpoint.person_id)
            self.assertEqual(endpoint.state, "ACTIVE")
            self.assertIsNotNone(message)
            self.assertEqual(
                db.scalar(select(PersonIdentifier).where(
                    PersonIdentifier.person_id == person_b,
                    PersonIdentifier.normalized_value == "+4917612345678",
                )).person_id,
                person_b,
            )

    def test_retry_reuses_same_message_row(self):
        person_id = self.make_person("Retry", "+4917611111111")
        thread_id = self.service.ensure_person_thread(person_id)["thread_id"]
        self.service.worker.fail_send = True
        with self.assertRaises(RuntimeError):
            self.service.send_text(thread_id, "Retry me")
        with self.service.database.session() as db:
            first = db.scalar(select(ChatMessage).where(ChatMessage.thread_id == thread_id, ChatMessage.direction == "OUT"))
            message_id = first.message_id
            self.assertEqual(first.status, "FAILED")
        retried = self.service.retry(message_id)
        self.assertEqual(retried["message_id"], message_id)
        self.assertEqual(retried["status"], "SENT")
        with self.service.database.session() as db:
            count = db.scalar(select(func.count(ChatMessage.message_id)).where(
                ChatMessage.thread_id == thread_id,
                ChatMessage.direction == "OUT",
            ))
            self.assertEqual(count, 1)

    def test_early_read_receipt_correlates_to_pending_message_and_is_not_downgraded(self):
        person_id = self.make_person("Receipt", "+4917611111111")
        thread_id = self.service.ensure_person_thread(person_id)["thread_id"]
        self.service.worker.receipt_before_return = True
        result = self.service.send_text(thread_id, "Fast receipt")
        self.assertEqual(result["status"], "READ")
        self.assertEqual(result["provider_message_id"], "wa-out-1")
        with self.service.database.session() as db:
            rows = db.scalars(select(ChatMessage).where(
                ChatMessage.thread_id == thread_id,
                ChatMessage.direction == "OUT",
            )).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].status, "READ")

    def test_thread_cursor_pagination_has_no_overlap(self):
        person_id = self.make_person("History", "+4917611111111")
        thread_id = self.service.ensure_person_thread(person_id)["thread_id"]
        with self.service.database.session() as db:
            for index in range(205):
                db.add(ChatMessage(
                    thread_id=thread_id,
                    direction="SYSTEM",
                    message_type="SYSTEM",
                    text=f"m{index}",
                    status="RECEIVED",
                ))
        first = self.service.thread(thread_id, 80)
        self.assertTrue(first["has_more"])
        self.assertIsNotNone(first["next_before"])
        second = self.service.thread(thread_id, 80, first["next_before"])
        first_ids = {x["message_id"] for x in first["messages"]}
        second_ids = {x["message_id"] for x in second["messages"]}
        self.assertFalse(first_ids & second_ids)

    def test_presence_event_is_mapped_to_its_thread(self):
        person_a = self.make_person("Anna", "+4917611111111")
        person_b = self.make_person("Beate", "+4917622222222")
        thread_a = self.service.ensure_person_thread(person_a)["thread_id"]
        self.service.ensure_person_thread(person_b)
        with self.service.database.session() as db:
            endpoint = db.scalar(select(ContactEndpoint).where(ContactEndpoint.person_id == person_a))
            endpoint.provider_address = "4917611111111@s.whatsapp.net"
        subscriber = self.service.events.subscribe()
        try:
            self.service._worker_event({
                "type": "presence",
                "payload": {
                    "jid": "4917611111111@s.whatsapp.net",
                    "participant": "4917611111111@s.whatsapp.net",
                    "state": "composing",
                },
            })
            event = subscriber.get_nowait()
            self.assertEqual(event["type"], "presence")
            self.assertEqual(event["thread_id"], thread_a)
            self.assertEqual(event["person_id"], person_a)
            self.assertNotIn("jid", event)
        finally:
            self.service.events.unsubscribe(subscriber)


class LanAccessTests(unittest.TestCase):
    def test_remote_api_and_archive_require_pairing_cookie(self):
        with tempfile.TemporaryDirectory() as td, patch("ladies_v9.messaging.service_v92.BaileysWorkerManager", FakeWorker):
            root = Path(td)
            data = root / "data"
            data.mkdir(parents=True)
            (data / "secret.txt").write_text("private", encoding="utf-8")
            from ladies_v9.web_v92 import create_app
            app = create_app(db_path=root / "app.sqlite3", data_dir=data)
            client = app.test_client()
            remote = {"REMOTE_ADDR": "192.168.1.50"}

            self.assertEqual(client.get("/api/dashboard", environ_base=remote).status_code, 401)
            self.assertEqual(client.get("/archive/secret.txt", environ_base=remote).status_code, 401)
            self.assertEqual(client.get("/api/dashboard").status_code, 200)

            token = app.config["LAN_ACCESS_TOKEN"]
            paired = client.get(f"/pair/{token}", environ_base=remote)
            self.assertEqual(paired.status_code, 302)
            self.assertEqual(client.get("/api/dashboard", environ_base=remote).status_code, 200)
            self.assertEqual(client.get("/archive/secret.txt", environ_base=remote).status_code, 200)


if __name__ == "__main__":
    unittest.main()
