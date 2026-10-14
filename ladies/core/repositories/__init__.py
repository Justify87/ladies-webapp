from __future__ import annotations

from dataclasses import dataclass

from .archive import ArchiveRepository
from .history import HistoryRepository
from .person_read import PersonReadRepository
from .persons import PersonRepository
from .encounter_data import EncounterAwarePersonRepository, EncounterDataRepository
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
        # Importing the repository package registers additive feature tables on
        # Base.metadata before schema reconciliation happens here.
        database.create_schema()
        settings = SettingsRepository(database)
        person_read = PersonReadRepository(database)
        encounter_data = EncounterDataRepository(database)
        encounter_data.backfill_legacy()
        return cls(
            search=SearchRepository(database),
            persons=EncounterAwarePersonRepository(
                database,
                archive,
                settings,
                person_read,
                encounter_data,
            ),
            history=HistoryRepository(database),
            settings=settings,
            archive=ArchiveRepository(database, archive),
        )


__all__ = [
    "ArchiveRepository",
    "EncounterDataRepository",
    "HistoryRepository",
    "PersonReadRepository",
    "PersonRepository",
    "RepositoryBundle",
    "SearchRepository",
    "SettingsRepository",
]
