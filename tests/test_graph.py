from __future__ import annotations

from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from coding_agent.graph import create_agent_graph

THREAD = {"configurable": {"thread_id": "default"}}


def _input(text: str, workspace: Path) -> dict:
    return {
        "messages": [HumanMessage(content=text)],
        "workspace": str(workspace),
        "tool_rounds": 0,
    }


def test_graph_has_only_model_between_start_and_end() -> None:
    graph = create_agent_graph(FakeListChatModel(responses=["ok"]))
    edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}

    assert edges == {("__start__", "model"), ("model", "__end__")}


def test_graph_merges_multiple_turns() -> None:
    graph = create_agent_graph(
        FakeListChatModel(responses=["one", "two"]),
        checkpointer=InMemorySaver(),
    )

    graph.invoke(_input("hello", Path("C:/work")), config=THREAD)
    second = graph.invoke(_input("again", Path("C:/work")), config=THREAD)

    assert [message.content for message in second["messages"]] == [
        "hello",
        "one",
        "again",
        "two",
    ]


def test_sqlite_restores_history_after_graph_rebuild(tmp_path: Path) -> None:
    database = tmp_path / "checkpoints.sqlite3"

    with SqliteSaver.from_conn_string(str(database)) as checkpointer:
        graph = create_agent_graph(
            FakeListChatModel(responses=["persisted answer"]),
            checkpointer=checkpointer,
        )
        graph.invoke(_input("persist this", tmp_path), config=THREAD)

    with SqliteSaver.from_conn_string(str(database)) as checkpointer:
        graph = create_agent_graph(
            FakeListChatModel(responses=["resumed answer"]),
            checkpointer=checkpointer,
        )
        result = graph.invoke(_input("continue", tmp_path), config=THREAD)

    assert [message.content for message in result["messages"]] == [
        "persist this",
        "persisted answer",
        "continue",
        "resumed answer",
    ]
