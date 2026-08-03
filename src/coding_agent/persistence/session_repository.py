"""SQLite session metadata repository adapter."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from langgraph.checkpoint.sqlite import SqliteSaver

from coding_agent.providers.base import ModelSelection
from coding_agent.sessions.models import Session, SessionId
from coding_agent.sessions.service import SessionConflictError


class SQLiteSessionRepository:
    def __init__(self, connection: sqlite3.Connection, checkpointer: SqliteSaver) -> None:
        self.connection = connection
        self.checkpointer = checkpointer

    def setup(self) -> None:
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS coding_agent_sessions (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    provider_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS coding_agent_app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def create(self, session: Session) -> None:
        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO coding_agent_sessions
                        (id, name, provider_id, model_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    self._values(session),
                )
        except sqlite3.IntegrityError:
            raise SessionConflictError(f"会话名称已存在：{session.name}") from None

    def get(self, session_id: SessionId) -> Session | None:
        row = self.connection.execute(
            """
            SELECT id, name, provider_id, model_id, created_at, updated_at
            FROM coding_agent_sessions WHERE id = ?
            """,
            (str(session_id),),
        ).fetchone()
        return self._session(row) if row is not None else None

    def list(self) -> tuple[Session, ...]:
        rows = self.connection.execute(
            """
            SELECT id, name, provider_id, model_id, created_at, updated_at
            FROM coding_agent_sessions ORDER BY updated_at DESC, id ASC
            """
        ).fetchall()
        return tuple(self._session(row) for row in rows)

    def update(self, session: Session) -> None:
        try:
            with self.connection:
                cursor = self.connection.execute(
                    """
                    UPDATE coding_agent_sessions
                    SET name = ?, provider_id = ?, model_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        session.name,
                        session.model.provider_id,
                        session.model.model_id,
                        session.updated_at.isoformat(),
                        str(session.id),
                    ),
                )
                if cursor.rowcount != 1:
                    raise KeyError(str(session.id))
        except sqlite3.IntegrityError:
            raise SessionConflictError(f"会话名称已存在：{session.name}") from None

    def delete(self, session_id: SessionId) -> None:
        self.checkpointer.delete_thread(str(session_id))
        with self.connection:
            self.connection.execute(
                "DELETE FROM coding_agent_sessions WHERE id = ?", (str(session_id),)
            )
            self.connection.execute(
                "DELETE FROM coding_agent_app_state WHERE key = 'active_session_id' AND value = ?",
                (str(session_id),),
            )

    def get_active_id(self) -> SessionId | None:
        row = self.connection.execute(
            "SELECT value FROM coding_agent_app_state WHERE key = 'active_session_id'"
        ).fetchone()
        return SessionId(row[0]) if row is not None else None

    def set_active_id(self, session_id: SessionId) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO coding_agent_app_state (key, value)
                VALUES ('active_session_id', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(session_id),),
            )

    def has_checkpoint(self, session_id: SessionId) -> bool:
        config = {"configurable": {"thread_id": str(session_id)}}
        return self.checkpointer.get_tuple(config) is not None

    @staticmethod
    def _values(session: Session) -> tuple[str, str, str, str, str, str]:
        return (
            str(session.id), session.name, session.model.provider_id, session.model.model_id,
            session.created_at.isoformat(), session.updated_at.isoformat(),
        )

    @staticmethod
    def _session(row: tuple[str, str, str, str, str, str]) -> Session:
        return Session(
            SessionId(row[0]), row[1], ModelSelection(row[2], row[3]),
            datetime.fromisoformat(row[4]), datetime.fromisoformat(row[5]),
        )


__all__ = ["SQLiteSessionRepository"]
