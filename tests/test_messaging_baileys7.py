from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from ladies_v9.models import Person, PersonIdentifier, PersonUserState
from ladies_v9.messaging.models import ContactEndpoint


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

    def dependency_status(self): return True, None
    def start(self): self.state = "CONNECTED"
    def stop(self): pass
    def status(self):
        return {"state": self.state, "detail": self.detail, "qr_available": False, "account": self.account, "last_connected_at": self.last_connected_at}
    def command(self, command_type, timeout=15, **payload): return {"ok": True, "payload": {}}


class Baileys7IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = patch("ladies_v9.messaging.service_v92.BaileysWorkerManager", FakeWorker)
        self.addCleanup(patcher.stop)
        patcher.start()
        from ladies_v9.messaging.service_v92 import MessagingService
        self.MessagingService = MessagingService
        self.service = MessagingService(self.root / "app.sqlite3", self.root / "data")

    def tearDown(self):
        self.tmp.cleanup()

    def test_lid_with_phone_alt_uses_phone_for_identity_but_preserves_lid_address(self):
        with self.service.database.session() as db:
            person = Person(display_name="Vanessa")
            db.add(person); db.flush(); db.add(PersonUserState(person_id=person.person_id))
            db.add(PersonIdentifier(person_id=person.person_id, kind="PHONE", normalized_value="+4917612345678", raw_value="+49 176 12345678"))
            person_id = person.person_id
        self.service._ingest_message({
            "provider_message_id": "lid-msg-1",
            "jid": "123456789012345@lid",
            "phone_jid": "4917612345678@s.whatsapp.net",
            "from_me": False,
            "text": "Hi",
            "message_type": "TEXT",
        })
        with self.service.database.session() as db:
            endpoint = db.scalar(select(ContactEndpoint).where(ContactEndpoint.provider_address == "123456789012345@lid"))
            self.assertIsNotNone(endpoint)
            self.assertEqual(endpoint.normalized_phone, "+4917612345678")
            self.assertEqual(endpoint.person_id, person_id)

    def test_lid_without_phone_is_kept_unassigned_without_fake_phone_identifier(self):
        self.service._ingest_message({
            "provider_message_id": "lid-msg-2",
            "jid": "998877665544332@lid",
            "phone_jid": None,
            "from_me": False,
            "text": "Unbekannt",
            "message_type": "TEXT",
        })
        with self.service.database.session() as db:
            endpoint = db.scalar(select(ContactEndpoint).where(ContactEndpoint.provider_address == "998877665544332@lid"))
            self.assertIsNotNone(endpoint)
            self.assertIsNone(endpoint.normalized_phone)
            self.assertIsNone(endpoint.person_id)
        self.assertEqual(len(self.service.unassigned()), 1)

    def test_assigning_lid_only_endpoint_does_not_create_fake_phone_identifier(self):
        with self.service.database.session() as db:
            person = Person(display_name="Manuell")
            db.add(person); db.flush(); db.add(PersonUserState(person_id=person.person_id)); person_id = person.person_id
        self.service._ingest_message({
            "provider_message_id": "lid-msg-3",
            "jid": "112233445566778@lid",
            "from_me": False,
            "text": "Hallo",
            "message_type": "TEXT",
        })
        endpoint_id = self.service.unassigned()[0]["endpoint"]["endpoint_id"]
        self.service.assign_endpoint(endpoint_id, person_id)
        with self.service.database.session() as db:
            phones = db.scalars(select(PersonIdentifier).where(PersonIdentifier.person_id == person_id, PersonIdentifier.kind == "PHONE")).all()
            self.assertEqual(phones, [])

    def test_typing_preference_persists(self):
        self.service.set_typing_enabled(False)
        second = self.MessagingService(self.root / "app.sqlite3", self.root / "data")
        self.assertFalse(second.typing_enabled)


if __name__ == "__main__":
    unittest.main()
