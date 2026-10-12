from __future__ import annotations

from contextlib import contextmanager
import logging
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

LOGGER = logging.getLogger("ladies_explorer.database")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.path}",
            future=True,
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        event.listen(self.engine, "connect", self._configure_sqlite)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        LOGGER.info("Database configured path=%s", self.path.resolve())

    @staticmethod
    def _configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    def create_schema(self) -> None:
        LOGGER.debug("Ensuring database schema")
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            LOGGER.exception("Database transaction rolled back")
            raise
        finally:
            session.close()
