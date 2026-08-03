from __future__ import annotations

from pathlib import Path
from typing import Sequence

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from coding_agent.agents.coding.context import ContextManager
from coding_agent.agents.coding.graph import create_agent_graph
from coding_agent.agents.coding.state import LongTermMemory


THREAD = {"configurable": {"thread_id": "memory-test"}}


class RecordingCompactor:
    def __init__(self) -> None:
        self.messages: list[AnyMessage] = []

    def compact(
        self,
        messages: Sequence[AnyMessage],
        previous_memory: LongTermMemory | None,
    ) -> LongTermMemory:
        self.messages = list(messages)
        return {
            "conversation_summary": "The user selected SQLite.",
            "session_decisions": ["Use SQLite for persistence."],
            "project_constraints": ["Preserve the public CLI."],
        }


class FailingCompactor:
    def compact(
        self,
        messages: Sequence[AnyMessage],
        previous_memory: LongTermMemory | None,
    ) -> LongTermMemory:
        raise RuntimeError("summary unavailable")


def _input(text: str, workspace: Path) -> dict:
    return {
        "messages": [HumanMessage(content=text)],
        "workspace": str(workspace),
        "tool_rounds": 0,
    }


def test_context_manager_keeps_messages_when_compaction_fails() -> None:
    messages = [
        HumanMessage(content="old request"),
        AIMessage(content="old answer"),
        HumanMessage(content="new request"),
    ]
    update = ContextManager(
        FailingCompactor(), max_chars=1, keep_recent_turns=1
    ).compact({"messages": messages, "workspace": "C:/work", "tool_rounds": 0})

    assert "messages" not in update
    assert update["context_compaction_error"] == "model_error"


def test_graph_compacts_history_and_persists_memory_across_rebuild(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory.sqlite3"
    compactor = RecordingCompactor()

    with SqliteSaver.from_conn_string(str(database)) as checkpointer:
        first = create_agent_graph(
            FakeListChatModel(responses=["old answer"]),
            checkpointer=checkpointer,
            context_compactor=compactor,
            context_max_chars=1,
            context_keep_recent_turns=1,
        )
        first.invoke(_input("old request", tmp_path), config=THREAD)

    with SqliteSaver.from_conn_string(str(database)) as checkpointer:
        second = create_agent_graph(
            FakeListChatModel(responses=["new answer"]),
            checkpointer=checkpointer,
            context_compactor=compactor,
            context_max_chars=1,
            context_keep_recent_turns=1,
        )
        result = second.invoke(_input("new request", tmp_path), config=THREAD)

    assert [message.content for message in compactor.messages] == [
        "old request",
        "old answer",
    ]
    assert [message.content for message in result["messages"]] == [
        "new request",
        "new answer",
    ]
    assert result["memory"]["session_decisions"] == ["Use SQLite for persistence."]
    assert result["memory"]["project_constraints"] == ["Preserve the public CLI."]
    assert result["context_compactions"] == 1
    assert result["discarded_message_count"] == 2


def test_default_model_compactor_merges_and_deduplicates_memory(tmp_path: Path) -> None:
    compaction_json = (
        '{"conversation_summary":"A compact summary.",'
        '"session_decisions":["Use SQLite.","Use SQLite."],'
        '"project_constraints":["Keep tests green."]}'
    )
    graph = create_agent_graph(
        FakeListChatModel(responses=[compaction_json, "new answer"]),
        context_max_chars=1,
        context_keep_recent_turns=1,
    )
    messages = [
        HumanMessage(content="old request"),
        AIMessage(content="old answer"),
        HumanMessage(content="new request"),
    ]

    result = graph.invoke(
        {
            **_input("new request", tmp_path),
            "messages": messages,
            "memory": {
                "conversation_summary": "Previous summary.",
                "session_decisions": ["Use SQLite."],
                "project_constraints": [],
            },
        }
    )

    assert result["memory"] == {
        "conversation_summary": "A compact summary.",
        "session_decisions": ["Use SQLite."],
        "project_constraints": ["Keep tests green."],
    }
