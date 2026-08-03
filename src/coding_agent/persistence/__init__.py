"""Persistence adapters for checkpoints and session metadata."""

from coding_agent.persistence.checkpoints import SQLitePersistence, open_sqlite_persistence
from coding_agent.persistence.session_repository import SQLiteSessionRepository

__all__ = ["SQLitePersistence", "SQLiteSessionRepository", "open_sqlite_persistence"]
