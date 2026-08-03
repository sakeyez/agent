from langchain_core.messages import AIMessage, HumanMessage

from coding_agent.agents.coding.routing import route_after_model


def _state(message: AIMessage, *, tool_rounds: int = 0) -> dict:
    return {
        "messages": [HumanMessage(content="request"), message],
        "workspace": "C:/work",
        "tool_rounds": tool_rounds,
        "termination_reason": None,
    }


def test_route_ends_for_final_answer() -> None:
    assert route_after_model(_state(AIMessage(content="done")), max_tool_rounds=8) == "__end__"


def test_route_executes_tool_calls_with_budget_remaining() -> None:
    message = AIMessage(
        content="",
        tool_calls=[{"id": "call-1", "name": "read_file", "args": {"path": "a.py"}}],
    )

    assert route_after_model(_state(message, tool_rounds=7), max_tool_rounds=8) == "tools"


def test_route_uses_limit_node_when_budget_is_exhausted() -> None:
    message = AIMessage(
        content="",
        tool_calls=[{"id": "call-1", "name": "read_file", "args": {"path": "a.py"}}],
    )

    assert route_after_model(_state(message, tool_rounds=8), max_tool_rounds=8) == "tool_limit"
