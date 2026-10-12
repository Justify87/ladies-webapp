from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ladies_v9.models import Person, PersonIdentifier, PersonUserState
from ladies_v9.messaging.models import ChatMessage


class QuietWorker:
    def __init__(self, worker_dir, auth_dir, event_handler):
        self.proc = None
        self.state = "NOT_CONFIGURED"
        self.detail = None
        self.qr = None
        self.account = None
        self.last_connected_at = None

    def dependency_status(self):
        return True, None

    def status(self):
        return {
            "state": self.state,
            "detail": self.detail,
            "qr_available": False,
            "account": None,
            "last_connected_at": None,
        }

    def start(self):
        return None

    def stop(self):
        return None


class MessageVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patcher = patch("ladies_v9.messaging.service.WhatsAppGateway", QuietWorker)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        from ladies_v9.messaging import MessagingService
        self.service = MessagingService(self.root / "test.sqlite3", self.root / "data")

    def tearDown(self):
        self.tmp.cleanup()

    def person(self, name: str, phone: str, *, like: bool = False, favorite: bool = False) -> int:
        with self.service.database.session() as db:
            person = Person(display_name=name)
            db.add(person)
            db.flush()
            db.add(PersonUserState(
                person_id=person.person_id,
                preference="LIKE" if like else "NONE",
                favorite=favorite,
            ))
            db.add(PersonIdentifier(
                person_id=person.person_id,
                kind="PHONE",
                normalized_value=phone,
                raw_value=phone,
            ))
            return person.person_id

    def add_outgoing(self, person_id: int, text: str) -> None:
        thread = self.service.ensure_person_thread(person_id)
        with self.service.database.session() as db:
            db.add(ChatMessage(
                thread_id=thread["thread_id"],
                endpoint_id=thread["endpoint"]["endpoint_id"],
                direction="OUT",
                message_type="TEXT",
                text=text,
                status="SENT",
            ))

    def test_started_chats_are_listed_regardless_of_person_state_favorites_first(self):
        liked = self.person("Like", "+4917610000001", like=True)
        favorite = self.person("Favorit", "+4917610000002", favorite=True)
        untouched_like = self.person("Nur gelikt", "+4917610000003", like=True)
        neutral_contact = self.person("Neutral", "+4917610000004")

        self.add_outgoing(liked, "Hallo Like")
        self.add_outgoing(favorite, "Hallo Favorit")
        self.service.ensure_person_thread(untouched_like)
        self.add_outgoing(neutral_contact, "Hallo Neutral")

        items = self.service.threads("ALL")
        self.assertEqual([item["display_name"] for item in items], ["Favorit", "Like", "Neutral"])
        self.assertTrue(items[0]["favorite"])
        self.assertEqual(items[1]["preference"], "LIKE")
        self.assertEqual(items[2]["preference"], "NONE")
        self.assertNotIn("Nur gelikt", [item["display_name"] for item in items])


if __name__ == "__main__":
    unittest.main()
