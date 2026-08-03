"""Shared SQLite lifecycle for LangGraph checkpoints and session metadata."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from coding_agent.persistence.session_repository import SQLiteSessionRepository


@dataclass(frozen=True, slots=True)
class SQLitePersistence:
    checkpointer: SqliteSaver
    sessions: SQLiteSessionRepository


@contextmanager
def open_sqlite_persistence(path: Path) -> Iterator[SQLitePersistence]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False)
    try:
        checkpointer = SqliteSaver(connection)
        checkpointer.setup()
        sessions = SQLiteSessionRepository(connection, checkpointer)
        sessions.setup()
        yield SQLitePersistence(checkpointer, sessions)
    finally:
        connection.close()


__all__ = ["SQLitePersistence", "open_sqlite_persistence"]
