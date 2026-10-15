from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ladies_v9.database import Database
from ladies_v9.messaging.models import ChatMessage, ChatThread, ContactEndpoint
from ladies_v9.messaging.scope import (
    endpoint_is_ladies_scoped,
    event_is_ladies_scoped,
    hydrate_ladies_history,
    message_is_ladies_scoped,
    person_is_ladies_scoped,
    scoped_thread_items,
    thread_is_ladies_scoped,
)
from ladies_v9.models import Listing, Person, PersonIdentifier


class MessagingScopeRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "scope.sqlite3")
        self.database.create_schema()

        with self.database.session() as db:
            ladies = Person(display_name="Ladies")
            phone_match = Person(display_name="Phone Match")
            foreign = Person(display_name="Private Contact")
            db.add_all([ladies, phone_match, foreign])
            db.flush()

            ladies_listing = Listing(person_id=ladies.person_id, source="LADIES", external_listing_id="ladies-1")
            phone_listing = Listing(person_id=phone_match.person_id, source="LADIES", external_listing_id="ladies-2")
            db.add_all([ladies_listing, phone_listing])
            db.flush()
            db.add(PersonIdentifier(
                person_id=phone_match.person_id,
                listing_id=phone_listing.listing_id,
                kind="PHONE",
                normalized_value="+491701234567",
                raw_value="0170 1234567",
            ))

            ladies_endpoint = ContactEndpoint(
                person_id=ladies.person_id,
                normalized_phone="+491511111111",
                source_listing_id=ladies_listing.listing_id,
            )
            foreign_endpoint = ContactEndpoint(
                person_id=foreign.person_id,
                normalized_phone="+491522222222",
            )
            matched_unassigned = ContactEndpoint(
                person_id=None,
                normalized_phone="+491701234567",
            )
            private_unassigned = ContactEndpoint(
                person_id=None,
                normalized_phone="+491799999999",
            )
            db.add_all([ladies_endpoint, foreign_endpoint, matched_unassigned, private_unassigned])
            db.flush()

            ladies_thread = ChatThread(person_id=ladies.person_id)
            foreign_thread = ChatThread(person_id=foreign.person_id)
            matched_thread = ChatThread(person_id=None)
            private_thread = ChatThread(person_id=None)
            db.add_all([ladies_thread, foreign_thread, matched_thread, private_thread])
            db.flush()

            ladies_message = ChatMessage(
                thread_id=ladies_thread.thread_id,
                endpoint_id=ladies_endpoint.endpoint_id,
                direction="OUT",
                status="SENT",
                text="hello",
            )
            foreign_message = ChatMessage(
                thread_id=foreign_thread.thread_id,
                endpoint_id=foreign_endpoint.endpoint_id,
                direction="IN",
                status="RECEIVED",
                text="private",
            )
            matched_message = ChatMessage(
                thread_id=matched_thread.thread_id,
                endpoint_id=matched_unassigned.endpoint_id,
                direction="IN",
                status="RECEIVED",
                text="matched",
            )
            private_message = ChatMessage(
                thread_id=private_thread.thread_id,
                endpoint_id=private_unassigned.endpoint_id,
                direction="IN",
                status="RECEIVED",
                text="private-unassigned",
            )
            db.add_all([ladies_message, foreign_message, matched_message, private_message])
            db.flush()

            self.ladies_person_id = ladies.person_id
            self.foreign_person_id = foreign.person_id
            self.ladies_endpoint_id = ladies_endpoint.endpoint_id
            self.foreign_endpoint_id = foreign_endpoint.endpoint_id
            self.matched_endpoint_id = matched_unassigned.endpoint_id
            self.private_endpoint_id = private_unassigned.endpoint_id
            self.ladies_thread_id = ladies_thread.thread_id
            self.foreign_thread_id = foreign_thread.thread_id
            self.matched_thread_id = matched_thread.thread_id
            self.private_thread_id = private_thread.thread_id
            self.ladies_message_id = ladies_message.message_id
            self.foreign_message_id = foreign_message.message_id
            self.matched_message_id = matched_message.message_id
            self.private_message_id = private_message.message_id

    def tearDown(self) -> None:
        self.database.engine.dispose()
        self.tempdir.cleanup()

    def test_scope_is_ladies_only_and_phone_evidence_can_resolve_unassigned(self) -> None:
        self.assertTrue(person_is_ladies_scoped(self.database, self.ladies_person_id))
        self.assertFalse(person_is_ladies_scoped(self.database, self.foreign_person_id))

        self.assertTrue(endpoint_is_ladies_scoped(self.database, self.ladies_endpoint_id))
        self.assertFalse(endpoint_is_ladies_scoped(self.database, self.foreign_endpoint_id))
        self.assertTrue(endpoint_is_ladies_scoped(self.database, self.matched_endpoint_id))
        self.assertFalse(endpoint_is_ladies_scoped(self.database, self.private_endpoint_id))

        self.assertTrue(thread_is_ladies_scoped(self.database, self.ladies_thread_id))
        self.assertFalse(thread_is_ladies_scoped(self.database, self.foreign_thread_id))
        self.assertTrue(thread_is_ladies_scoped(self.database, self.matched_thread_id))
        self.assertFalse(thread_is_ladies_scoped(self.database, self.private_thread_id))

        self.assertTrue(message_is_ladies_scoped(self.database, self.ladies_message_id))
        self.assertFalse(message_is_ladies_scoped(self.database, self.foreign_message_id))
        self.assertTrue(message_is_ladies_scoped(self.database, self.matched_message_id))
        self.assertFalse(message_is_ladies_scoped(self.database, self.private_message_id))

    def test_thread_lists_and_events_do_not_leak_private_whatsapp(self) -> None:
        items = [
            {"thread_id": self.ladies_thread_id, "person_id": self.ladies_person_id, "endpoint": {"endpoint_id": self.ladies_endpoint_id}},
            {"thread_id": self.foreign_thread_id, "person_id": self.foreign_person_id, "endpoint": {"endpoint_id": self.foreign_endpoint_id}},
            {"thread_id": self.matched_thread_id, "person_id": None, "endpoint": {"endpoint_id": self.matched_endpoint_id}},
            {"thread_id": self.private_thread_id, "person_id": None, "endpoint": {"endpoint_id": self.private_endpoint_id}},
        ]
        self.assertEqual(
            [row["thread_id"] for row in scoped_thread_items(self.database, items, include_unassigned=False)],
            [self.ladies_thread_id],
        )
        self.assertEqual(
            [row["thread_id"] for row in scoped_thread_items(self.database, items, include_unassigned=True)],
            [self.ladies_thread_id, self.matched_thread_id],
        )

        self.assertTrue(event_is_ladies_scoped(self.database, {"type": "status", "state": "CONNECTED"}))
        self.assertTrue(event_is_ladies_scoped(self.database, {"type": "message", "thread_id": self.ladies_thread_id}))
        self.assertTrue(event_is_ladies_scoped(self.database, {"type": "message", "message_id": self.matched_message_id}))
        self.assertFalse(event_is_ladies_scoped(self.database, {"type": "message", "thread_id": self.foreign_thread_id}))
        self.assertFalse(event_is_ladies_scoped(self.database, {"type": "message", "message_id": self.private_message_id}))

    def test_identity_ingest_hydrates_history_without_opening_chat(self) -> None:
        class Gateway:
            state = "DISCONNECTED"

        class Service:
            def __init__(self, database):
                self.database = database
                self.gateway = Gateway()
                self.synced = []
                self.rehydrated = []

            def sync_person(self, person_id):
                self.synced.append(person_id)
                return []

            def rehydrate_recent_history(self, person_id, limit):
                self.rehydrated.append((person_id, limit))
                return {"person_id": person_id, "requested": 1, "restored": 1, "existing": 0}

        service = Service(self.database)
        result = hydrate_ladies_history(service, self.ladies_person_id, limit=10)
        self.assertEqual(service.synced, [self.ladies_person_id])
        self.assertEqual(service.rehydrated, [(self.ladies_person_id, 10)])
        self.assertEqual(result["restored"], 1)

        foreign = Service(self.database)
        result = hydrate_ladies_history(foreign, self.foreign_person_id, limit=10)
        self.assertEqual(foreign.synced, [])
        self.assertEqual(foreign.rehydrated, [])
        self.assertEqual(result["restored"], 0)


if __name__ == "__main__":
    unittest.main()
