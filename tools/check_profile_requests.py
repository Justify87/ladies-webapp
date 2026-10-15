from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select

from ladies_v9.database import Database
from ladies_v9.messaging.integration import merge_messaging_person
from ladies_v9.messaging.models import ChatMessage, ChatThread, ContactEndpoint
from ladies_v9.messaging.profile_requests import ProfileRequest, ProfileRequestService
from ladies_v9.models import Listing, Person, utcnow


class _Events:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def publish(self, payload: dict) -> None:
        self.items.append(dict(payload))


class _Gateway:
    state = "DISCONNECTED"


class _Messaging:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.gateway = _Gateway()
        self.events = _Events()
        self.mode = "success"
        self.dispatch_calls = 0
        self.retry_calls = 0
        self.ensure_listing_ids: list[int | None] = []

    def ensure_person_thread(self, person_id: int, listing_id: int | None = None) -> dict:
        self.ensure_listing_ids.append(listing_id)
        with self.database.session() as db:
            thread = db.scalar(select(ChatThread).where(ChatThread.person_id == int(person_id)))
            endpoint_query = select(ContactEndpoint).where(
                ContactEndpoint.person_id == int(person_id),
                ContactEndpoint.state == "ACTIVE",
            )
            endpoints = db.scalars(endpoint_query).all()
            endpoint = None
            if listing_id is not None:
                endpoint = next((item for item in endpoints if item.source_listing_id == int(listing_id)), None)
            endpoint = endpoint or next((item for item in endpoints if item.is_preferred), None) or (endpoints[0] if endpoints else None)
            return {
                "thread_id": thread.thread_id if thread else None,
                "endpoint": {"endpoint_id": endpoint.endpoint_id} if endpoint else None,
            }

    def _dispatch_text(self, message_id: int) -> dict:
        self.dispatch_calls += 1
        if self.mode == "clear-failure":
            raise ValueError("Kein gültiger WhatsApp-Endpunkt ausgewählt")
        if self.mode == "ambiguous-failure":
            with self.database.session() as db:
                message = db.get(ChatMessage, int(message_id))
                message.status = "FAILED"
                message.error = "WhatsApp hat nicht rechtzeitig geantwortet"
            raise RuntimeError("WhatsApp hat nicht rechtzeitig geantwortet")
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            message.status = "SENT"
            message.provider_message_id = f"provider-{message_id}"
            message.error = None
        return {"message_id": int(message_id), "status": "SENT"}

    def retry(self, message_id: int) -> dict:
        self.retry_calls += 1
        with self.database.session() as db:
            message = db.get(ChatMessage, int(message_id))
            if message is None or message.status != "FAILED":
                raise ValueError("Nur fehlgeschlagene Nachrichten können erneut gesendet werden")
            message.status = "PENDING"
            message.error = None
            message.provider_message_id = None
        return self._dispatch_text(message_id)


class ProfileRequestRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tempdir.name) / "requests.sqlite3")
        self.database.create_schema()
        with self.database.session() as db:
            source = Person(display_name="Source")
            target = Person(display_name="Target")
            db.add_all([source, target])
            db.flush()
            source_listing_a = Listing(person_id=source.person_id, source="LADIES", external_listing_id="ad-a")
            source_listing_b = Listing(person_id=source.person_id, source="LADIES", external_listing_id="ad-b")
            target_listing = Listing(person_id=target.person_id, source="LADIES", external_listing_id="target-ad")
            db.add_all([source_listing_a, source_listing_b, target_listing])
            db.flush()
            source_thread = ChatThread(person_id=source.person_id)
            target_thread = ChatThread(person_id=target.person_id)
            db.add_all([source_thread, target_thread])
            db.flush()
            db.add_all([
                ContactEndpoint(
                    person_id=source.person_id,
                    normalized_phone="+491510000001",
                    source_listing_id=source_listing_a.listing_id,
                    state="ACTIVE",
                    is_preferred=False,
                ),
                ContactEndpoint(
                    person_id=source.person_id,
                    normalized_phone="+491510000002",
                    source_listing_id=source_listing_b.listing_id,
                    state="ACTIVE",
                    is_preferred=True,
                ),
                ContactEndpoint(
                    person_id=target.person_id,
                    normalized_phone="+491510000003",
                    source_listing_id=target_listing.listing_id,
                    state="ACTIVE",
                    is_preferred=True,
                ),
            ])
            self.source_id = int(source.person_id)
            self.target_id = int(target.person_id)
            self.listing_a = int(source_listing_a.listing_id)
            self.listing_b = int(source_listing_b.listing_id)
            self.target_thread_id = int(target_thread.thread_id)
        self.messaging = _Messaging(self.database)
        self.service = ProfileRequestService(self.messaging)

    def tearDown(self) -> None:
        self.database.engine.dispose()
        self.tempdir.cleanup()

    def _message_count(self) -> int:
        with self.database.session() as db:
            return int(db.scalar(select(func.count(ChatMessage.message_id))) or 0)

    def _requests(self, person_id: int | None = None) -> list[ProfileRequest]:
        with self.database.session() as db:
            query = select(ProfileRequest)
            if person_id is not None:
                query = query.where(ProfileRequest.person_id == int(person_id))
            return list(db.scalars(query.order_by(ProfileRequest.request_id)).all())

    def test_success_is_idempotent_and_new_tokens_are_blocked_during_cooldown(self) -> None:
        first = self.service.send(self.source_id, listing_id=self.listing_a, request_token="token-one")
        self.assertTrue(first["sent"])
        self.assertFalse(first["can_send"])
        self.assertEqual(self.messaging.ensure_listing_ids, [self.listing_a])
        self.assertEqual(self._message_count(), 1)
        dispatches = self.messaging.dispatch_calls

        same = self.service.send(self.source_id, listing_id=self.listing_a, request_token="token-one")
        self.assertTrue(same["sent"])
        self.assertTrue(same["idempotent"])
        self.assertEqual(self.messaging.dispatch_calls, dispatches)

        blocked = self.service.send(self.source_id, listing_id=self.listing_a, request_token="token-two")
        self.assertFalse(blocked["sent"])
        self.assertTrue(blocked["cooldown"])
        self.assertEqual(self._message_count(), 1)
        self.assertEqual(self.messaging.dispatch_calls, dispatches)

    def test_clear_failure_is_retryable_and_reuses_the_same_chat_message(self) -> None:
        self.messaging.mode = "clear-failure"
        with self.assertRaises(ValueError):
            self.service.send(self.source_id, listing_id=self.listing_a, request_token="clear-failure")
        status = self.service.status(self.source_id)
        self.assertTrue(status["can_send"])
        self.assertTrue(status["retryable"])
        self.assertEqual(self._message_count(), 1)

        self.messaging.mode = "success"
        result = self.service.send(self.source_id, listing_id=self.listing_a, request_token="retry-click")
        self.assertTrue(result["sent"])
        self.assertTrue(result["retried"])
        self.assertEqual(self.messaging.retry_calls, 1)
        self.assertEqual(self._message_count(), 1, "retry must reuse the canonical failed message")

    def test_ambiguous_failure_never_becomes_immediately_retryable(self) -> None:
        self.messaging.mode = "ambiguous-failure"
        with self.assertRaises(RuntimeError):
            self.service.send(self.source_id, listing_id=self.listing_a, request_token="ambiguous")
        status = self.service.status(self.source_id)
        self.assertFalse(status["can_send"])
        self.assertTrue(status["uncertain"])
        self.assertFalse(status["retryable"])
        dispatches = self.messaging.dispatch_calls

        blocked = self.service.send(self.source_id, listing_id=self.listing_a, request_token="new-token")
        self.assertFalse(blocked["sent"])
        self.assertTrue(blocked["uncertain"])
        self.assertEqual(self.messaging.dispatch_calls, dispatches)
        with self.database.session() as db:
            row = self._latest_request(db, self.source_id)
            message = db.get(ChatMessage, int(row.message_id))
            self.assertEqual(message.status, "PENDING", "ambiguous sends must not expose the normal retry path")

    def test_pending_crash_state_recovers_when_the_linked_message_is_confirmed(self) -> None:
        self.messaging.mode = "ambiguous-failure"
        with self.assertRaises(RuntimeError):
            self.service.send(self.source_id, listing_id=self.listing_a, request_token="recover")
        with self.database.session() as db:
            row = self._latest_request(db, self.source_id)
            message = db.get(ChatMessage, int(row.message_id))
            row.status = "PENDING"
            message.status = "DELIVERED"
            message.provider_message_id = "provider-recovered"
        status = self.service.status(self.source_id)
        self.assertEqual(status["attempt_status"], "SENT")
        self.assertIsNotNone(status["last_request_at"])
        self.assertFalse(status["can_send"])

    def test_expired_cooldown_allows_new_request_and_avoids_immediate_variant_repeat(self) -> None:
        self.service.send(self.source_id, listing_id=self.listing_a, request_token="old")
        with self.database.session() as db:
            row = self._latest_request(db, self.source_id)
            old_variant = row.variant_id
            row.sent_at = utcnow() - timedelta(hours=25)
        result = self.service.send(self.source_id, listing_id=self.listing_a, request_token="new")
        self.assertTrue(result["sent"])
        rows = self._requests(self.source_id)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[-1].variant_id, old_variant)

    def test_listing_must_belong_to_person(self) -> None:
        with self.assertRaises(ValueError):
            self.service.send(self.source_id, listing_id=999999, request_token="bad-listing")
        self.assertEqual(self._message_count(), 0)

    def test_person_and_thread_merge_preserve_cooldown_and_request_history(self) -> None:
        self.service.send(self.source_id, listing_id=self.listing_b, request_token="merge-me")
        with self.database.session() as db:
            before = self._latest_request(db, self.source_id)
            request_id = int(before.request_id)
            message_id = int(before.message_id)
            changed = merge_messaging_person(db, self.source_id, self.target_id)
            self.assertTrue(changed)
        with self.database.session() as db:
            row = db.get(ProfileRequest, request_id)
            message = db.get(ChatMessage, message_id)
            self.assertEqual(row.person_id, self.target_id)
            self.assertEqual(row.thread_id, self.target_thread_id)
            self.assertEqual(message.thread_id, self.target_thread_id)
            self.assertEqual(row.status, "SENT")
        status = self.service.status(self.target_id)
        self.assertFalse(status["can_send"])
        self.assertIsNotNone(status["last_request_at"])

    @staticmethod
    def _latest_request(db, person_id: int) -> ProfileRequest:
        row = db.scalar(
            select(ProfileRequest)
            .where(ProfileRequest.person_id == int(person_id))
            .order_by(ProfileRequest.request_id.desc())
            .limit(1)
        )
        assert row is not None
        return row


if __name__ == "__main__":
    unittest.main()
