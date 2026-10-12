from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ladies_v9.models import Person, PersonIdentifier, PersonUserState


class ProbeWorker:
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
        if command_type == "contact_info":
            return {
                "ok": True,
                "payload": {
                    "exists": True,
                    "jid": "4917612345678@s.whatsapp.net",
                    "phone_jid": "4917612345678@s.whatsapp.net",
                    "profile_picture_url": "https://example.invalid/wa-avatar.jpg",
                    "detail": "registered",
                },
            }
        return {"ok": True, "payload": {}}


class WhatsAppChatStabilityTests(unittest.TestCase):
    def test_worker_keeps_required_lid_history_sync_and_contact_probe(self):
        source = (Path(__file__).parents[1] / "ladies" / "messaging" / "whatsapp_worker" / "worker.mjs").read_text(encoding="utf-8")
        self.assertIn("syncType !== proto.HistorySync.HistorySyncType.FULL", source)
        self.assertNotIn("shouldSyncHistoryMessage: () => false", source)
        self.assertIn("case 'contact_info'", source)
        self.assertIn("current.onWhatsApp(phoneJid)", source)
        self.assertIn("profilePictureUrl(target, 'preview', 5000)", source)
        self.assertIn("Diese Nummer ist nicht bei WhatsApp registriert", source)

    def test_chat_layer_sits_above_generic_full_layers(self):
        css = (Path(__file__).parents[1] / "ladies" / "static" / "messaging.css").read_text(encoding="utf-8")
        self.assertIn(".messaging-chat-layer{z-index:140", css)

    def test_profile_contact_probe_returns_registration_and_picture(self):
        with tempfile.TemporaryDirectory() as td, patch("ladies_v9.messaging.service_runtime.BaileysWorkerManager", ProbeWorker):
            from ladies_v9.app import create_app

            root = Path(td)
            app = create_app(db_path=root / "app.sqlite3", data_dir=root / "data")
            service = app.extensions["ladies_messaging"]
            service.worker.state = "CONNECTED"
            with service.database.session() as db:
                person = Person(display_name="Probe")
                db.add(person)
                db.flush()
                db.add(PersonUserState(person_id=person.person_id, preference="LIKE"))
                db.add(PersonIdentifier(
                    person_id=person.person_id,
                    kind="PHONE",
                    normalized_value="+4917612345678",
                    raw_value="+49 176 12345678",
                ))
                person_id = person.person_id

            client = app.test_client()
            summary = client.get(f"/api/messaging/person/{person_id}").get_json()
            endpoint_id = summary["selected_endpoint"]["endpoint_id"]
            response = client.get(f"/api/messaging/endpoints/{endpoint_id}/probe")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertIs(payload["exists"], True)
            self.assertEqual(payload["display_phone"], "+49 176 12345678")
            self.assertEqual(payload["profile_picture_url"], "https://example.invalid/wa-avatar.jpg")
            command, worker_payload = service.worker.commands[-1]
            self.assertEqual(command, "contact_info")
            self.assertEqual(worker_payload["jid"], "4917612345678@s.whatsapp.net")

    def test_default_file_logging_is_warning_first(self):
        launcher = (Path(__file__).parents[1] / "ladies_explorer.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("LADIES_LOG_LEVEL") or "WARNING"', launcher)
        self.assertIn("maxBytes=2_000_000, backupCount=3", launcher)


if __name__ == "__main__":
    unittest.main()
