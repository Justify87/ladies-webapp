from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ladies_v9.models import Person, utcnow
from ladies_v9.messaging.models import ChatMessage, ChatThread, ContactEndpoint


class QuietWorker:
    def __init__(self, worker_dir, auth_dir, event_handler):
        self.worker_dir = Path(worker_dir)
        self.auth_dir = Path(auth_dir)
        self.event_handler = event_handler
        self.proc = None
        self.state = "NOT_CONFIGURED"
        self.detail = None
        self.qr = None
        self.account = None
        self.last_connected_at = None
        self.commands = []

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
        return {"ok": True, "payload": {}}


class ChatUiPolishTests(unittest.TestCase):
    def make_app(self, td):
        from ladies_v9.app import create_app
        root = Path(td)
        return create_app(db_path=root / "app.sqlite3", data_dir=root / "data")

    def test_neutral_person_with_outgoing_message_is_always_in_inbox(self):
        with tempfile.TemporaryDirectory() as td, patch("ladies_v9.messaging.service.WhatsAppGateway", QuietWorker):
            app = self.make_app(td)
            service = app.extensions["ladies_messaging"]
            with service.database.session() as db:
                person = Person(display_name="Neutraler Chat")
                db.add(person)
                db.flush()
                endpoint = ContactEndpoint(
                    person_id=person.person_id,
                    normalized_phone="+4917612345678",
                    display_phone="+49 176 12345678",
                    state="ACTIVE",
                )
                db.add(endpoint)
                db.flush()
                thread = ChatThread(person_id=person.person_id)
                db.add(thread)
                db.flush()
                message = ChatMessage(
                    thread_id=thread.thread_id,
                    endpoint_id=endpoint.endpoint_id,
                    direction="OUT",
                    message_type="TEXT",
                    text="Hallo",
                    status="SENT",
                )
                db.add(message)
                db.flush()
                thread.last_message_at = message.timestamp
                thread.updated_at = utcnow()
                thread_id = thread.thread_id

            items = service.threads("ALL")
            self.assertEqual([item["thread_id"] for item in items], [thread_id])
            self.assertEqual(items[0]["display_name"], "Neutraler Chat")
            self.assertFalse(items[0]["favorite"])
            self.assertIsNone(items[0]["preference"])

    def test_empty_placeholder_thread_stays_out_of_inbox(self):
        with tempfile.TemporaryDirectory() as td, patch("ladies_v9.messaging.service.WhatsAppGateway", QuietWorker):
            app = self.make_app(td)
            service = app.extensions["ladies_messaging"]
            with service.database.session() as db:
                person = Person(display_name="Nur geöffnet")
                db.add(person)
                db.flush()
                db.add(ChatThread(person_id=person.person_id))

            self.assertEqual(service.threads("ALL"), [])

    def test_presence_subscription_is_skipped_while_reconnecting(self):
        with tempfile.TemporaryDirectory() as td, patch("ladies_v9.messaging.service.WhatsAppGateway", QuietWorker):
            app = self.make_app(td)
            service = app.extensions["ladies_messaging"]
            service.worker.state = "RECONNECTING"
            service.subscribe_presence(999)
            self.assertEqual(service.worker.commands, [])

    def test_mobile_chat_polish_has_keyboard_and_presence_guards(self):
        source = (Path(__file__).parents[1] / "ladies" / "static" / "profile_messaging_ui.js").read_text(encoding="utf-8")
        self.assertIn("window.visualViewport", source)
        self.assertIn("6500", source)
        self.assertIn("presenceExpiredAt", source)
        self.assertIn("grid-template-columns:minmax(0,1fr) 50px", source)
        self.assertIn("composer-plus{display:none", source)
        self.assertIn("M4 5.2 20 12 4 18.8", source)
        self.assertIn("Hier erscheinen alle Unterhaltungen", source)


if __name__ == "__main__":
    unittest.main()
