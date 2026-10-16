from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
from typing import Iterator

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

LOGGER = logging.getLogger("ladies_explorer.database")
CURRENT_SCHEMA_VERSION = 1
MAX_MIGRATION_BACKUPS = 3


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

    def schema_version(self) -> int:
        with self.engine.connect() as connection:
            return int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())

    @staticmethod
    def _application_tables(connection) -> set[str]:
        rows = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).all()
        return {str(row[0]) for row in rows}

    @staticmethod
    def _set_schema_version(connection, version: int) -> None:
        connection.exec_driver_sql(f"PRAGMA user_version = {int(version)}")

    def _backup_before_migration(self, from_version: int, to_version: int) -> Path:
        backup_dir = self.path.parent / "migration-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        suffix = self.path.suffix or ".sqlite3"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        backup = backup_dir / (
            f"{self.path.stem}.schema-v{from_version}-to-v{to_version}-{stamp}{suffix}"
        )
        with sqlite3.connect(str(self.path)) as source, sqlite3.connect(str(backup)) as target:
            source.backup(target)
        LOGGER.warning("Database migration backup created path=%s", backup.resolve())

        backups = sorted(
            backup_dir.glob(f"{self.path.stem}.schema-v*-to-v*-*{suffix}"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old in backups[MAX_MIGRATION_BACKUPS:]:
            try:
                old.unlink()
            except OSError:
                LOGGER.warning("Could not remove old migration backup path=%s", old)
        return backup

    @staticmethod
    def _safe_column_definition(connection, column) -> str:
        if column.primary_key or column.unique or column.foreign_keys:
            raise RuntimeError(
                f"Spalte {column.table.name}.{column.name} benötigt eine explizite Migration "
                "(Primary Key, Unique oder Foreign Key)."
            )
        if not column.nullable and column.server_default is None:
            raise RuntimeError(
                f"Spalte {column.table.name}.{column.name} ist NOT NULL ohne Server-Default und "
                "kann nicht automatisch zu bestehenden Daten ergänzt werden."
            )

        preparer = connection.dialect.identifier_preparer
        type_sql = column.type.compile(dialect=connection.dialect)
        sql = f"{preparer.quote(column.name)} {type_sql}"
        if column.server_default is not None:
            default = column.server_default.arg
            if hasattr(default, "compile"):
                default_sql = str(default.compile(
                    dialect=connection.dialect,
                    compile_kwargs={"literal_binds": True},
                ))
            else:
                default_sql = str(default)
            sql += f" DEFAULT {default_sql}"
        if not column.nullable:
            sql += " NOT NULL"
        return sql

    def _add_safe_missing_columns(self, connection) -> None:
        preparer = connection.dialect.identifier_preparer
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            current_columns = {item["name"] for item in inspect(connection).get_columns(table.name)}
            for column in table.columns:
                if column.name in current_columns:
                    continue
                definition = self._safe_column_definition(connection, column)
                LOGGER.warning("Adding legacy-safe column %s.%s", table.name, column.name)
                connection.exec_driver_sql(
                    f"ALTER TABLE {preparer.quote(table.name)} ADD COLUMN {definition}"
                )
                current_columns.add(column.name)

    @staticmethod
    def _ensure_declared_indexes(connection) -> None:
        for table in Base.metadata.sorted_tables:
            for index in table.indexes:
                index.create(bind=connection, checkfirst=True)

    def _migrate_0_to_1(self, connection) -> None:
        # Schema version 0 upgrades are additive and only append SQLite-safe columns.
        Base.metadata.create_all(connection)
        self._add_safe_missing_columns(connection)
        self._ensure_declared_indexes(connection)

    def _run_migration(self, connection, from_version: int) -> int:
        migrations = {
            0: self._migrate_0_to_1,
        }
        migration = migrations.get(from_version)
        if migration is None:
            raise RuntimeError(
                f"Keine Datenbankmigration von Schema-Version {from_version} vorhanden."
            )
        migration(connection)
        return from_version + 1

    def _validate_schema(self, connection) -> None:
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())
        missing_tables = sorted(set(Base.metadata.tables) - existing_tables)
        missing_columns: list[str] = []
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            current_columns = {item["name"] for item in inspect(connection).get_columns(table.name)}
            for column in table.columns:
                if column.name not in current_columns:
                    missing_columns.append(f"{table.name}.{column.name}")
        if missing_tables or missing_columns:
            details = []
            if missing_tables:
                details.append("fehlende Tabellen: " + ", ".join(missing_tables))
            if missing_columns:
                details.append("fehlende Spalten: " + ", ".join(missing_columns))
            raise RuntimeError("Datenbankschema ist unvollständig: " + "; ".join(details))

    def create_schema(self) -> None:
        """Create or upgrade the local SQLite schema before repositories use it.

        PRAGMA user_version marks the schema version. Migrations create a backup
        before changing an existing schema. Additive tables are always reconciled
        so feature modules can register tables without a version bump.
        """
        with self.engine.connect() as connection:
            current = int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())
            has_tables = bool(self._application_tables(connection))

        if current > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"Datenbank-Schema {current} ist neuer als diese Anwendung "
                f"({CURRENT_SCHEMA_VERSION}). Bitte eine neuere App-Version verwenden."
            )

        if not has_tables:
            LOGGER.info("Creating fresh database schema version=%s", CURRENT_SCHEMA_VERSION)
            with self.engine.begin() as connection:
                Base.metadata.create_all(connection)
                self._set_schema_version(connection, CURRENT_SCHEMA_VERSION)
                self._validate_schema(connection)
            return

        if current < CURRENT_SCHEMA_VERSION:
            backup = self._backup_before_migration(current, CURRENT_SCHEMA_VERSION)
            LOGGER.warning(
                "Upgrading database schema from=%s to=%s backup=%s",
                current,
                CURRENT_SCHEMA_VERSION,
                backup,
            )
            try:
                with self.engine.begin() as connection:
                    while current < CURRENT_SCHEMA_VERSION:
                        current = self._run_migration(connection, current)
                        self._set_schema_version(connection, current)
                    self._validate_schema(connection)
            except Exception:
                LOGGER.exception(
                    "Automatic database migration failed; original backup remains at %s",
                    backup.resolve(),
                )
                raise
            LOGGER.info("Database schema upgrade complete version=%s", current)
            return

        # Reconcile additive tables registered by feature modules.
        with self.engine.begin() as connection:
            Base.metadata.create_all(connection)
            self._ensure_declared_indexes(connection)
            self._validate_schema(connection)

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
