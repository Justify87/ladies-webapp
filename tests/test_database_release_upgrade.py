from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect, select

from ladies_v9.database import CURRENT_SCHEMA_VERSION, Database
from ladies_v9.models import Base, Person, PersonUserState, utcnow
from ladies_v9.messaging.models import ChatMessage, ChatThread, ContactEndpoint


class ReleaseShapeUpgradeTests(unittest.TestCase):
    def test_v923_shaped_database_upgrades_without_losing_core_or_chat_rows(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ladies.sqlite3"
            engine = create_engine(f"sqlite:///{path}", future=True)

            # V9.2.x already had the core + endpoint/thread/message tables. The
            # V9.3 PR adds chat_message_meta/chat_reactions additively.
            legacy_tables = [
                table for name, table in Base.metadata.tables.items()
                if name not in {"chat_message_meta", "chat_reactions"}
            ]
            Base.metadata.create_all(engine, tables=legacy_tables)
            with engine.begin() as connection:
                connection.exec_driver_sql("PRAGMA user_version = 0")
                person_id = connection.execute(
                    Person.__table__.insert().values(
                        display_name="Legacy",
                        first_seen_at=utcnow(),
                        last_seen_at=utcnow(),
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                ).inserted_primary_key[0]
                connection.execute(PersonUserState.__table__.insert().values(
                    person_id=person_id,
                    preference="NONE",
                    favorite=False,
                    repeat_interest="UNKNOWN",
                    updated_at=utcnow(),
                ))
                endpoint_id = connection.execute(ContactEndpoint.__table__.insert().values(
                    person_id=person_id,
                    provider="WHATSAPP",
                    normalized_phone="+4917612345000",
                    provider_address="4917612345000@s.whatsapp.net",
                    first_seen_at=utcnow(),
                    last_seen_at=utcnow(),
                    is_preferred=False,
                    state="ACTIVE",
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )).inserted_primary_key[0]
                thread_id = connection.execute(ChatThread.__table__.insert().values(
                    person_id=person_id,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                    unread_count=1,
                )).inserted_primary_key[0]
                message_id = connection.execute(ChatMessage.__table__.insert().values(
                    thread_id=thread_id,
                    endpoint_id=endpoint_id,
                    provider_message_id="legacy-provider-id",
                    direction="IN",
                    message_type="TEXT",
                    text="Altbestand",
                    timestamp=utcnow(),
                    status="RECEIVED",
                    created_at=utcnow(),
                )).inserted_primary_key[0]
            engine.dispose()

            database = Database(path)
            database.create_schema()
            self.assertEqual(database.schema_version(), CURRENT_SCHEMA_VERSION)
            tables = set(inspect(database.engine).get_table_names())
            self.assertIn("chat_message_meta", tables)
            self.assertIn("chat_reactions", tables)

            with database.session() as db:
                self.assertEqual(db.get(Person, person_id).display_name, "Legacy")
                message = db.get(ChatMessage, message_id)
                self.assertEqual(message.text, "Altbestand")
                self.assertEqual(message.provider_message_id, "legacy-provider-id")

            backups = list((path.parent / "migration-backups").glob("*.sqlite3"))
            self.assertEqual(len(backups), 1)


if __name__ == "__main__":
    unittest.main()
