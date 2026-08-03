from __future__ import annotations

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage

from coding_agent.agents.coding.graph import create_agent_graph
from coding_agent.persistence import open_sqlite_persistence
from coding_agent.providers import ModelSelection
from coding_agent.sessions import SessionService


def test_existing_default_checkpoint_is_registered_without_rewriting(tmp_path) -> None:
    database = tmp_path / "checkpoints.sqlite3"
    config = {"configurable": {"thread_id": "default"}}
    payload = {
        "messages": [HumanMessage(content="legacy")],
        "workspace": str(tmp_path),
        "tool_rounds": 0,
    }
    with open_sqlite_persistence(database) as persistence:
        graph = create_agent_graph(
            FakeListChatModel(responses=["persisted"]),
            checkpointer=persistence.checkpointer,
        )
        graph.invoke(payload, config=config)

    with open_sqlite_persistence(database) as persistence:
        sessions = SessionService(
            persistence.sessions, ModelSelection("kimi", "new-default")
        )
        current = sessions.initialize()
        snapshot = create_agent_graph(
            FakeListChatModel(responses=["unused"]),
            checkpointer=persistence.checkpointer,
        ).get_state(config)

    assert current.id == "default"
    assert [message.content for message in snapshot.values["messages"]] == [
        "legacy",
        "persisted",
    ]


def test_active_session_and_model_survive_repository_reopen(tmp_path) -> None:
    database = tmp_path / "sessions.sqlite3"
    default_model = ModelSelection("kimi", "default")
    with open_sqlite_persistence(database) as persistence:
        service = SessionService(persistence.sessions, default_model)
        service.initialize()
        work = service.create("work")
        service.change_model(
            str(work.id),
            ModelSelection("openai-compatible", "coder"),
            has_unfinished_task=False,
        )

    with open_sqlite_persistence(database) as persistence:
        restored = SessionService(persistence.sessions, default_model)
        current = restored.initialize()

    assert current.name == "work"
    assert current.model.reference == "openai-compatible:coder"
