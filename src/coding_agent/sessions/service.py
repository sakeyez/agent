"""Use cases for creating, loading, listing, and deleting sessions."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from coding_agent.providers.base import ModelSelection
from coding_agent.sessions.models import Session, SessionId


class SessionError(ValueError):
    """A session error safe to display to an interface."""


class SessionNotFoundError(SessionError):
    pass


class SessionConflictError(SessionError):
    pass


class SessionRepository(Protocol):
    def create(self, session: Session) -> None: ...
    def get(self, session_id: SessionId) -> Session | None: ...
    def list(self) -> Sequence[Session]: ...
    def update(self, session: Session) -> None: ...
    def delete(self, session_id: SessionId) -> None: ...
    def get_active_id(self) -> SessionId | None: ...
    def set_active_id(self, session_id: SessionId) -> None: ...
    def has_checkpoint(self, session_id: SessionId) -> bool: ...


_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")


def _validated_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > 80 or _CONTROL_CHARACTER.search(name):
        raise SessionError("会话名称必须是 1-80 个不含控制字符的文本")
    return name


class SessionService:
    def __init__(self, repository: SessionRepository, default_model: ModelSelection) -> None:
        self.repository = repository
        self.default_model = default_model

    def initialize(self) -> Session:
        sessions = list(self.repository.list())
        active_id = self.repository.get_active_id()
        if active_id is not None:
            active = self.repository.get(active_id)
            if active is not None:
                return active

        default_id = SessionId("default")
        default = self.repository.get(default_id)
        if default is None and self.repository.has_checkpoint(default_id):
            default = self._new_session("default", self.default_model, default_id)
            self._create(default)
            sessions.append(default)
        if not sessions:
            default = self._new_session("default", self.default_model, default_id)
            self._create(default)
            sessions = [default]
        active = sorted(sessions, key=lambda item: item.updated_at, reverse=True)[0]
        self.repository.set_active_id(active.id)
        return active

    def current(self) -> Session:
        active_id = self.repository.get_active_id()
        session = self.repository.get(active_id) if active_id is not None else None
        return session if session is not None else self.initialize()

    def list_sessions(self) -> tuple[Session, ...]:
        return tuple(self.repository.list())

    def create(self, name: str | None = None) -> Session:
        session_name = _validated_name(name) if name is not None else self._generated_name()
        self._ensure_unique_name(session_name)
        session = self._new_session(session_name, self.default_model)
        self._create(session)
        self.repository.set_active_id(session.id)
        return session

    def resolve(self, reference: str) -> Session:
        value = reference.strip()
        if not value:
            raise SessionNotFoundError("缺少会话名称或 ID")
        sessions = list(self.repository.list())
        by_name = [item for item in sessions if item.name.casefold() == value.casefold()]
        if len(by_name) == 1:
            return by_name[0]
        exact_id = [item for item in sessions if str(item.id) == value]
        if len(exact_id) == 1:
            return exact_id[0]
        by_prefix = [item for item in sessions if str(item.id).startswith(value)]
        if len(by_prefix) == 1:
            return by_prefix[0]
        if len(by_prefix) > 1:
            raise SessionConflictError(f"会话 ID 前缀不唯一：{value}")
        raise SessionNotFoundError(f"未找到会话：{value}")

    def activate(self, reference: str) -> Session:
        session = self.resolve(reference)
        self.repository.set_active_id(session.id)
        return session

    def rename(self, reference: str, name: str) -> Session:
        session = self.resolve(reference)
        validated = _validated_name(name)
        self._ensure_unique_name(validated, excluding=session.id)
        updated = self._updated(session, name=validated)
        self._update(updated)
        return updated

    def touch(self, session_id: SessionId) -> Session:
        session = self.repository.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"未找到会话：{session_id}")
        updated = self._updated(session)
        self.repository.update(updated)
        return updated

    def change_model(
        self,
        reference: str,
        selection: ModelSelection,
        *,
        has_unfinished_task: bool,
    ) -> Session:
        if has_unfinished_task:
            raise SessionConflictError("会话存在未完成任务，请先恢复完成或使用 /cancel")
        session = self.resolve(reference)
        updated = self._updated(session, model=selection)
        self.repository.update(updated)
        return updated

    def delete(self, reference: str, *, has_unfinished_task: bool) -> Session:
        if has_unfinished_task:
            raise SessionConflictError("会话存在未完成任务，请先使用 /cancel")
        session = self.resolve(reference)
        was_active = self.repository.get_active_id() == session.id
        self.repository.delete(session.id)
        remaining = list(self.repository.list())
        if not was_active:
            return self.current()
        if remaining:
            replacement = remaining[0]
        else:
            replacement = self._new_session("default", self.default_model, SessionId("default"))
            self._create(replacement)
        self.repository.set_active_id(replacement.id)
        return replacement

    def _generated_name(self) -> str:
        base = datetime.now(UTC).strftime("session-%Y%m%d-%H%M%S")
        existing = {item.name.casefold() for item in self.repository.list()}
        if base.casefold() not in existing:
            return base
        index = 2
        while f"{base}-{index}".casefold() in existing:
            index += 1
        return f"{base}-{index}"

    def _ensure_unique_name(
        self, name: str, *, excluding: SessionId | None = None
    ) -> None:
        folded = name.casefold()
        if any(
            item.id != excluding and item.name.casefold() == folded
            for item in self.repository.list()
        ):
            raise SessionConflictError(f"会话名称已存在：{name}")

    @staticmethod
    def _new_session(
        name: str, model: ModelSelection, session_id: SessionId | None = None
    ) -> Session:
        now = datetime.now(UTC)
        return Session(session_id or SessionId(uuid4().hex), name, model, now, now)

    @staticmethod
    def _updated(
        session: Session,
        *,
        name: str | None = None,
        model: ModelSelection | None = None,
    ) -> Session:
        return Session(
            session.id,
            name if name is not None else session.name,
            model if model is not None else session.model,
            session.created_at,
            datetime.now(UTC),
        )

    def _create(self, session: Session) -> None:
        try:
            self.repository.create(session)
        except SessionConflictError:
            raise
        except Exception as error:
            raise SessionError(f"创建会话失败：{str(error)[:200]}") from None

    def _update(self, session: Session) -> None:
        try:
            self.repository.update(session)
        except SessionConflictError:
            raise
        except Exception as error:
            raise SessionError(f"更新会话失败：{str(error)[:200]}") from None


__all__ = [
    "SessionConflictError",
    "SessionError",
    "SessionNotFoundError",
    "SessionRepository",
    "SessionService",
]
