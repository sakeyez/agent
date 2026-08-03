from __future__ import annotations

import sqlite3

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from coding_agent.persistence.session_repository import SQLiteSessionRepository
from coding_agent.providers import ModelSelection
from coding_agent.sessions import SessionConflictError, SessionService


@pytest.fixture
def service(tmp_path):
    connection = sqlite3.connect(tmp_path / "sessions.sqlite3")
    saver = SqliteSaver(connection)
    saver.setup()
    repository = SQLiteSessionRepository(connection, saver)
    repository.setup()
    yield SessionService(repository, ModelSelection("kimi", "default-model"))
    connection.close()


def test_session_lifecycle_persists_active_model_and_unique_names(service) -> None:
    default = service.initialize()
    work = service.create("Work Session")

    assert default.id == "default"
    assert service.current() == work
    assert service.resolve("work session") == work
    assert service.resolve(str(work.id)[:8]) == work

    renamed = service.rename(str(work.id), "Renamed")
    changed = service.change_model(
        str(renamed.id),
        ModelSelection("openai-compatible", "coder"),
        has_unfinished_task=False,
    )
    assert changed.model.reference == "openai-compatible:coder"

    with pytest.raises(SessionConflictError, match="已存在"):
        service.rename(str(default.id), "RENAMED")

    service.create("Ä")
    with pytest.raises(SessionConflictError, match="已存在"):
        service.create("ä")


def test_delete_refuses_unfinished_task_and_recreates_last_session(service) -> None:
    default = service.initialize()
    with pytest.raises(SessionConflictError, match="/cancel"):
        service.delete(str(default.id), has_unfinished_task=True)

    replacement = service.delete(str(default.id), has_unfinished_task=False)
    assert replacement.id == "default"
    assert service.current() == replacement
