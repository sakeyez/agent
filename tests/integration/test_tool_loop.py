from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import Field

from coding_agent.graph import create_agent_graph


class ScriptedToolModel(BaseChatModel):
    responses: list[AIMessage]
    response_index: int = 0
    bound_tools: list[dict[str, Any]] = Field(default_factory=list)
    bound_tool_sets: list[set[str]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self.bound_tools = list(tools)
        self.bound_tool_sets.append({schema["function"]["name"] for schema in tools})
        return self

    def _generate(self, *args, **kwargs) -> ChatResult:
        response = self.responses[self.response_index]
        self.response_index += 1
        return ChatResult(generations=[ChatGeneration(message=response)])


def _input(workspace: Path) -> dict:
    return {
        "messages": [HumanMessage(content="inspect the workspace")],
        "workspace": str(workspace),
        "tool_rounds": 0,
        "termination_reason": None,
    }


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": name, "args": args}],
    )


def test_graph_executes_tool_and_returns_result_to_model(tmp_path: Path) -> None:
    (tmp_path / "example.py").write_text("print('hello')\n", encoding="utf-8")
    model = ScriptedToolModel(
        responses=[
            _tool_call("read-1", "read_file", {"path": "example.py"}),
            AIMessage(content="The file prints hello."),
        ]
    )

    result = create_agent_graph(model).invoke(_input(tmp_path))

    tool_message = next(message for message in result["messages"] if isinstance(message, ToolMessage))
    assert tool_message.tool_call_id == "read-1"
    assert tool_message.status == "success"
    assert "1: print('hello')" in str(tool_message.content)
    assert result["messages"][-1].content == "The file prints hello."
    assert result["tool_rounds"] == 1
    assert result["termination_reason"] == "completed"
    assert {
        "list_files",
        "read_file",
        "search_text",
        "apply_patch",
        "run_command",
        "git_diff",
        "report_step_result",
    } in model.bound_tool_sets


def test_graph_executes_all_calls_in_one_round_in_order(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
    model = ScriptedToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "list-1", "name": "list_files", "args": {}},
                    {
                        "id": "search-1",
                        "name": "search_text",
                        "args": {"query": "needle"},
                    },
                ],
            ),
            AIMessage(content="done"),
        ]
    )

    result = create_agent_graph(model).invoke(_input(tmp_path))
    tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]

    assert [message.tool_call_id for message in tool_messages] == ["list-1", "search-1"]
    assert result["tool_rounds"] == 1


def test_tool_error_is_returned_and_model_can_recover(tmp_path: Path) -> None:
    (tmp_path / "real.txt").write_text("recovered\n", encoding="utf-8")
    model = ScriptedToolModel(
        responses=[
            _tool_call("missing-1", "read_file", {"path": "missing.txt"}),
            _tool_call("real-1", "read_file", {"path": "real.txt"}),
            AIMessage(content="Recovered after correcting the path."),
        ]
    )

    result = create_agent_graph(model).invoke(_input(tmp_path))
    tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]

    assert [message.status for message in tool_messages] == ["error", "success"]
    assert result["tool_rounds"] == 2
    assert result["messages"][-1].content == "Recovered after correcting the path."


def test_unknown_tool_is_a_recoverable_tool_error(tmp_path: Path) -> None:
    model = ScriptedToolModel(
        responses=[
            _tool_call("bad-1", "not_registered", {}),
            AIMessage(content="That tool is unavailable."),
        ]
    )

    result = create_agent_graph(model).invoke(_input(tmp_path))
    tool_message = next(message for message in result["messages"] if isinstance(message, ToolMessage))

    assert tool_message.status == "error"
    assert "unknown_tool" in str(tool_message.content)
    assert result["termination_reason"] == "completed"


def test_chat_mode_rejects_write_tool_even_if_model_hallucinates_it(tmp_path: Path) -> None:
    patch = "--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+new\n"
    model = ScriptedToolModel(
        responses=[
            _tool_call("patch-1", "apply_patch", {"patch": patch}),
            AIMessage(content="Approval is required."),
        ]
    )

    result = create_agent_graph(model).invoke(_input(tmp_path))
    tool_message = next(message for message in result["messages"] if isinstance(message, ToolMessage))

    assert tool_message.status == "error"
    assert "tool_not_allowed" in str(tool_message.content)
    assert tool_message.artifact["error_code"] == "tool_not_allowed"
    assert not (tmp_path / "new.txt").exists()


def test_round_limit_resolves_pending_call_then_forces_final_answer(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("content\n", encoding="utf-8")
    model = ScriptedToolModel(
        responses=[
            _tool_call("call-1", "read_file", {"path": "a.txt"}),
            _tool_call("call-2", "read_file", {"path": "a.txt"}),
            _tool_call("call-3", "read_file", {"path": "a.txt"}),
            AIMessage(content="Final answer from available evidence."),
        ]
    )

    result = create_agent_graph(model, max_tool_rounds=2).invoke(_input(tmp_path))
    tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]

    assert [message.tool_call_id for message in tool_messages] == ["call-1", "call-2", "call-3"]
    assert tool_messages[-1].status == "error"
    assert "tool_round_limit" in str(tool_messages[-1].content)
    assert result["tool_rounds"] == 2
    assert result["termination_reason"] == "max_tool_rounds"
    assert result["messages"][-1].content == "Final answer from available evidence."
    assert model.response_index == 4


def test_sqlite_restores_tool_history_and_resets_round_budget(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("persisted\n", encoding="utf-8")
    database = tmp_path / "agent.sqlite3"
    config = {"configurable": {"thread_id": "tool-history"}}

    with SqliteSaver.from_conn_string(str(database)) as checkpointer:
        first_model = ScriptedToolModel(
            responses=[
                _tool_call("first-read", "read_file", {"path": "a.txt"}),
                AIMessage(content="first answer"),
            ]
        )
        first = create_agent_graph(first_model, checkpointer=checkpointer).invoke(
            _input(tmp_path), config=config
        )
        assert first["tool_rounds"] == 1

    with SqliteSaver.from_conn_string(str(database)) as checkpointer:
        second_model = ScriptedToolModel(
            responses=[
                _tool_call("second-read", "read_file", {"path": "a.txt"}),
                AIMessage(content="second answer"),
            ]
        )
        second = create_agent_graph(second_model, checkpointer=checkpointer).invoke(
            {
                "messages": [HumanMessage(content="inspect again")],
                "workspace": str(tmp_path),
                "tool_rounds": 0,
                "termination_reason": None,
            },
            config=config,
        )

    tool_messages = [message for message in second["messages"] if isinstance(message, ToolMessage)]
    assert [message.tool_call_id for message in tool_messages] == ["first-read", "second-read"]
    assert second["tool_rounds"] == 1
    assert second["messages"][-1].content == "second answer"
