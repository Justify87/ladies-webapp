from __future__ import annotations

from dataclasses import dataclass

from .archive import ArchiveRepository
from .history import HistoryRepository
from .person_read import PersonReadRepository
from .persons import PersonRepository
from .search import SearchRepository
from .settings import SettingsRepository


@dataclass(slots=True)
class RepositoryBundle:
    search: SearchRepository
    persons: PersonRepository
    history: HistoryRepository
    settings: SettingsRepository
    archive: ArchiveRepository

    @classmethod
    def from_dependencies(cls, database, archive):
        # Schema creation/migration is an application-composition concern. It
        # must happen before any repository constructor touches settings/tables.
        database.create_schema()
        settings = SettingsRepository(database)
        person_read = PersonReadRepository(database)
        return cls(
            search=SearchRepository(database),
            persons=PersonRepository(database, archive, settings, person_read),
            history=HistoryRepository(database),
            settings=settings,
            archive=ArchiveRepository(database, archive),
        )


__all__ = [
    "ArchiveRepository",
    "HistoryRepository",
    "PersonReadRepository",
    "PersonRepository",
    "RepositoryBundle",
    "SearchRepository",
    "SettingsRepository",
]
