from __future__ import annotations

from dataclasses import dataclass

from .archive import ArchiveRepository
from .history import HistoryRepository
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
    def from_store(cls, store):
        settings = SettingsRepository(store.database)
        return cls(
            search=SearchRepository(store.database),
            persons=PersonRepository(
                store.database,
                store.archive,
                settings,
                store,
            ),
            history=HistoryRepository(store.database),
            settings=settings,
            archive=ArchiveRepository(store.database, store.archive),
        )


__all__ = [
    "ArchiveRepository",
    "HistoryRepository",
    "PersonRepository",
    "RepositoryBundle",
    "SearchRepository",
    "SettingsRepository",
]
