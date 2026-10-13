import tempfile
import unittest
from pathlib import Path

from ladies_v9.database import Database

from sqlalchemy import select

from ladies_v9.models import Person, PersonUserState, PriceItem, PriceList
from ladies_v9.messaging.models import ChatMessage, ChatThread
from ladies_v9.pricing import PriceIntelligenceService
from ladies_v9.pricing.integration import _merge_pricing_in_session
from ladies_v9.pricing.models import PriceExtraction


def _seed(service):
    with service.database.session() as db:
        person = Person(display_name="Test")
        db.add(person)
        db.flush()
        db.add(PersonUserState(person_id=person.person_id))
        thread = ChatThread(person_id=person.person_id)
        db.add(thread)
        db.flush()
        db.add(ChatMessage(thread_id=thread.thread_id, direction="OUT", message_type="TEXT", text="Was kostet es?", status="SENT"))
        db.add(ChatMessage(thread_id=thread.thread_id, direction="IN", message_type="TEXT", text="30 min 100 €, 1h 160 €", status="RECEIVED"))
        db.add(ChatMessage(thread_id=thread.thread_id, direction="IN", message_type="TEXT", text="Küssen inklusive", status="RECEIVED"))
        return person.person_id, thread.thread_id


def _add_incoming(service, thread_id, text):
    with service.database.session() as db:
        row = ChatMessage(thread_id=thread_id, direction="IN", message_type="TEXT", text=text, status="RECEIVED")
        db.add(row)
        db.flush()
        return row.message_id


def _database(path):
    database = Database(path)
    database.create_schema()
    return database


class PricingServiceTests(unittest.TestCase):
    def test_end_to_end_proposal_accept_and_history(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            service = PriceIntelligenceService(_database(root / "test.db"), root / "data")
            person_id, thread_id = _seed(service)
            signal = service.signal(thread_id, allow_ocr=False)
            self.assertTrue(signal["strong"])
            proposal = service.create_proposal(thread_id, trigger_type="MANUAL")
            self.assertEqual(len(proposal["proposal"]["offers"]), 2)
            accepted = service.accept(proposal["price_extraction_id"], {})
            self.assertEqual(accepted["status"], "ACCEPTED")
            current = service.current(person_id)
            self.assertEqual(current["price_list_id"], accepted["accepted_price_list_id"])
            self.assertEqual(len(current["proposal"]["offers"]), 2)
            history = service.history(person_id)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["revision"]["source_extraction_id"], proposal["price_extraction_id"])

    def test_same_prices_do_not_create_duplicate_revision(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            service = PriceIntelligenceService(_database(root / "test.db"), root / "data")
            person_id, thread_id = _seed(service)
            first = service.create_proposal(thread_id)
            service.accept(first["price_extraction_id"], {})
            second = service.create_proposal(thread_id, trigger_type="MANUAL", seed_message_id=2)
            result = service.accept(second["price_extraction_id"], {})
            self.assertEqual(result["status"], "ACCEPTED_NO_CHANGE")
            self.assertEqual(len(service.history(person_id)), 1)

    def test_handled_context_no_longer_triggers_magic_signal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            service = PriceIntelligenceService(_database(root / "test.db"), root / "data")
            _, thread_id = _seed(service)
            first = service.create_proposal(thread_id)
            service.accept(first["price_extraction_id"], {})
            signal = service.signal(thread_id, allow_ocr=False)
            self.assertFalse(signal["strong"])
            self.assertGreater(signal["handled_through_message_id"], 0)

    def test_rejected_context_no_longer_triggers_magic_signal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            service = PriceIntelligenceService(_database(root / "test.db"), root / "data")
            _, thread_id = _seed(service)
            proposal = service.create_proposal(thread_id)
            service.reject(proposal["price_extraction_id"])
            signal = service.signal(thread_id, allow_ocr=False)
            self.assertFalse(signal["strong"])

    def test_stale_proposal_cannot_overwrite_newer_revision(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            service = PriceIntelligenceService(_database(root / "test.db"), root / "data")
            _, thread_id = _seed(service)
            baseline = service.create_proposal(thread_id)
            service.accept(baseline["price_extraction_id"], {})
            message_id = _add_incoming(service, thread_id, "1h jetzt 180 €")
            first = service.create_proposal(thread_id, seed_message_id=message_id)
            second = service.create_proposal(thread_id, seed_message_id=message_id)
            accepted = service.accept(first["price_extraction_id"], {})
            self.assertEqual(accepted["status"], "ACCEPTED")
            stale = service.accept(second["price_extraction_id"], {})
            self.assertEqual(stale["status"], "STALE")

    def test_partial_revision_preserves_legacy_price_items_exactly(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            service = PriceIntelligenceService(_database(root / "test.db"), root / "data")
            person_id, thread_id = _seed(service)
            with service.database.session() as db:
                price_list = PriceList(person_id=person_id, source="MANUAL", currency="EUR")
                db.add(price_list)
                db.flush()
                db.add(PriceItem(price_list_id=price_list.price_list_id, item_type="BASE", label="30 Minuten", duration_minutes=30, amount_cents=10000))
                db.add(PriceItem(price_list_id=price_list.price_list_id, item_type="EXTRA", label="Sonderwunsch", service="LegacyService", amount_cents=4000, note="beibehalten"))
            message_id = _add_incoming(service, thread_id, "30 min jetzt 120 €")
            proposal = service.create_proposal(thread_id, seed_message_id=message_id)
            result = service.accept(proposal["price_extraction_id"], {})
            self.assertEqual(result["status"], "ACCEPTED")
            with service.database.session() as db:
                rows = db.scalars(select(PriceItem).where(PriceItem.price_list_id == result["accepted_price_list_id"]).order_by(PriceItem.price_item_id)).all()
                legacy = [x for x in rows if x.item_type == "EXTRA"]
                self.assertEqual(len(legacy), 1)
                self.assertEqual(legacy[0].service, "LegacyService")
                self.assertEqual(legacy[0].amount_cents, 4000)
                self.assertEqual(legacy[0].note, "beibehalten")

    def test_identity_merge_moves_extraction_before_source_deletes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            service = PriceIntelligenceService(_database(root / "test.db"), root / "data")
            with service.database.session() as db:
                source = Person(display_name="Source")
                target = Person(display_name="Target")
                db.add_all([source, target])
                db.flush()
                source_thread = ChatThread(person_id=source.person_id)
                target_thread = ChatThread(person_id=target.person_id)
                db.add_all([source_thread, target_thread])
                db.flush()
                extraction = PriceExtraction(
                    person_id=source.person_id,
                    thread_id=source_thread.thread_id,
                    original_proposal_json='{"schema_version":1,"currency":"EUR","update_mode":"UNKNOWN","offers":[],"service_rules":[],"conditions":[],"uncertainties":[]}',
                    context_fingerprint="x" * 64,
                )
                db.add(extraction)
                db.flush()
                extraction_id = extraction.price_extraction_id
                _merge_pricing_in_session(db, source.person_id, target.person_id)
                db.delete(source_thread)
                db.delete(source)
                db.flush()
                moved = db.get(PriceExtraction, extraction_id)
                self.assertIsNotNone(moved)
                self.assertEqual(moved.person_id, target.person_id)
                self.assertEqual(moved.thread_id, target_thread.thread_id)


if __name__ == "__main__":
    unittest.main()
