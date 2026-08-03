"""Pure routing decisions between coding agent graph nodes."""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import AIMessage

from coding_agent.agents.coding.state import CodingAgentState

ModelRoute = Literal["tools", "tool_limit", "__end__"]


def route_after_model(state: CodingAgentState, *, max_tool_rounds: int) -> ModelRoute:
    """Choose the next node solely from state and the configured budget."""

    messages = state.get("messages", [])
    if not messages or not isinstance(messages[-1], AIMessage):
        return "__end__"
    if not messages[-1].tool_calls:
        return "__end__"
    if state.get("tool_rounds", 0) >= max_tool_rounds:
        return "tool_limit"
    return "tools"
