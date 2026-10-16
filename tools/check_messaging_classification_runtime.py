from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import func, select

from ladies_v9.database import Database
from ladies_v9.models import Listing, Person, PersonIdentifier
from ladies_v9.messaging.classification import (
    LADIES_CONFIRMED,
    NON_LADIES,
    UNKNOWN,
    MessagingClassifier,
    reconcile_thread_in_session,
)
from ladies_v9.messaging.integration import merge_messaging_person
from ladies_v9.messaging.models import (
    ChatClassification,
    ChatMessage,
    ChatThread,
    ContactEndpoint,
    EndpointClassification,
)
from ladies_v9.messaging.profile_requests import ProfileRequest  # noqa: F401 - registers model metadata
from ladies_v9.messaging.service_classified import MessagingService


def _person(database: Database, name: str, phone: str | None = None) -> int:
    with database.session() as db:
        person = Person(display_name=name)
        db.add(person)
        db.flush()
        db.add(
            Listing(
                person_id=int(person.person_id),
                source="LADIES",
                external_listing_id=f"runtime-{name.lower()}-{person.person_id}",
            )
        )
        if phone:
            db.add(
                PersonIdentifier(
                    person_id=int(person.person_id),
                    kind="PHONE",
                    normalized_value=phone,
                    raw_value=phone,
                )
            )
        return int(person.person_id)


def _plain_person(database: Database, name: str) -> int:
    with database.session() as db:
        person = Person(display_name=name)
        db.add(person)
        db.flush()
        return int(person.person_id)


def _conversation(
    database: Database,
    *,
    person_id: int | None,
    phone: str,
    text: str,
) -> tuple[int, int, int]:
    with database.session() as db:
        endpoint = ContactEndpoint(
            person_id=person_id,
            provider="WHATSAPP",
            normalized_phone=phone,
            display_phone=phone,
            state="ACTIVE",
        )
        thread = ChatThread(person_id=person_id)
        db.add_all([endpoint, thread])
        db.flush()
        message = ChatMessage(
            thread_id=int(thread.thread_id),
            endpoint_id=int(endpoint.endpoint_id),
            direction="IN",
            message_type="TEXT",
            text=text,
            status="RECEIVED",
        )
        db.add(message)
        db.flush()
        return int(thread.thread_id), int(endpoint.endpoint_id), int(message.message_id)


def _service(database: Database) -> MessagingService:
    service = object.__new__(MessagingService)
    service.database = database
    return service


def _assert_status(database: Database, thread_id: int, endpoint_id: int, status: str) -> None:
    with database.session() as db:
        assert db.get(ChatClassification, thread_id).status == status
        assert db.get(EndpointClassification, endpoint_id).status == status


def check_race_safe_bootstrap(database: Database) -> None:
    with database.session() as db:
        thread = ChatThread(person_id=None)
        db.add(thread)
        db.flush()
        thread_id = int(thread.thread_id)

    def classify_once() -> str:
        with database.session() as db:
            thread = db.get(ChatThread, thread_id)
            assert thread is not None
            return reconcile_thread_in_session(db, thread).status

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: classify_once(), range(4)))
    assert statuses == [UNKNOWN] * 4
    with database.session() as db:
        count = int(
            db.scalar(
                select(func.count(ChatClassification.thread_id)).where(
                    ChatClassification.thread_id == thread_id
                )
            )
            or 0
        )
        assert count == 1


def check_reads_are_side_effect_free(database: Database) -> None:
    classifier = MessagingClassifier(database)
    with database.session() as db:
        thread = ChatThread(person_id=None)
        db.add(thread)
        db.flush()
        thread_id = int(thread.thread_id)
    items = [{"thread_id": thread_id, "unread_count": 0}]
    assert classifier.filter_items(items, view="REVIEW")[0]["classification"] == UNKNOWN
    classifier.summary()
    with database.session() as db:
        assert db.get(ChatClassification, thread_id) is None


def check_server_rejects_non_ladies_assignment(database: Database) -> None:
    person_id = _plain_person(database, "PrivatePerson")
    thread_id, endpoint_id, _ = _conversation(
        database,
        person_id=None,
        phone="+491709999999",
        text="not a ladies identity",
    )
    service = _service(database)
    try:
        service.assign_endpoint(endpoint_id, person_id)
    except ValueError as exc:
        assert "Ladies.de-Provenienz" in str(exc)
    else:
        raise AssertionError("manual assignment accepted a person without Ladies.de provenance")
    with database.session() as db:
        assert db.get(ContactEndpoint, endpoint_id).person_id is None
        assert db.get(ChatThread, thread_id).person_id is None


def check_sticky_exclusion_and_manual_override(database: Database) -> None:
    phone = "+491701111111"
    person_id = _person(database, "Sticky", phone)
    thread_id, endpoint_id, _ = _conversation(
        database,
        person_id=None,
        phone=phone,
        text="private",
    )
    classifier = MessagingClassifier(database)
    classifier.set_thread(thread_id, NON_LADIES, method="runtime_manual_exclusion")
    _assert_status(database, thread_id, endpoint_id, NON_LADIES)

    with database.session() as db:
        assert db.get(ChatThread, thread_id).person_id is None
        assert db.get(ContactEndpoint, endpoint_id).person_id is None

    service = _service(database)
    service.sync_person(person_id)
    with database.session() as db:
        assert db.get(ChatThread, thread_id).person_id is None
        assert db.get(ContactEndpoint, endpoint_id).person_id is None
    _assert_status(database, thread_id, endpoint_id, NON_LADIES)

    service.assign_endpoint(endpoint_id, person_id)
    with database.session() as db:
        endpoint = db.get(ContactEndpoint, endpoint_id)
        assert endpoint is not None and endpoint.person_id == person_id
        target = db.scalar(select(ChatThread).where(ChatThread.person_id == person_id))
        assert target is not None
        target_thread_id = int(target.thread_id)
        assert db.get(ChatClassification, target_thread_id).status == LADIES_CONFIRMED
        assert db.get(EndpointClassification, endpoint_id).status == LADIES_CONFIRMED


def check_identity_merge_keeps_private_chat_separate(database: Database) -> None:
    source_id = _person(database, "MergeSource", "+491702222222")
    target_id = _person(database, "MergeTarget", "+491703333333")

    private_thread, private_endpoint, private_message = _conversation(
        database,
        person_id=target_id,
        phone="+491703333333",
        text="private target history",
    )
    classifier = MessagingClassifier(database)
    classifier.set_thread(private_thread, NON_LADIES, method="runtime_private_target")

    source_thread, source_endpoint, source_message = _conversation(
        database,
        person_id=source_id,
        phone="+491702222222",
        text="ladies source history",
    )
    classifier.reconcile()
    _assert_status(database, source_thread, source_endpoint, LADIES_CONFIRMED)

    with database.session() as db:
        assert merge_messaging_person(db, source_id, target_id)

    with database.session() as db:
        private = db.get(ChatThread, private_thread)
        assert private is not None and private.person_id is None
        assert db.get(ContactEndpoint, private_endpoint).person_id is None
        assert db.get(ChatClassification, private_thread).status == NON_LADIES
        assert db.get(ChatMessage, private_message).thread_id == private_thread

        canonical = db.scalar(select(ChatThread).where(ChatThread.person_id == target_id))
        assert canonical is not None
        canonical_id = int(canonical.thread_id)
        assert canonical_id != private_thread
        assert db.get(ChatClassification, canonical_id).status == LADIES_CONFIRMED
        assert db.get(ChatMessage, source_message).thread_id == canonical_id


def main() -> None:
    with TemporaryDirectory(prefix="ladies-msg-classification-") as tmp:
        database = Database(Path(tmp) / "runtime.sqlite3")
        database.create_schema()
        check_race_safe_bootstrap(database)
        check_reads_are_side_effect_free(database)
        check_server_rejects_non_ladies_assignment(database)
        check_sticky_exclusion_and_manual_override(database)
        check_identity_merge_keeps_private_chat_separate(database)
    print("messaging classification runtime checks passed")


if __name__ == "__main__":
    main()
