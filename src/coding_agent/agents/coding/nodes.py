"""Model, tool, and lifecycle nodes used by the coding agent graph."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import Runnable

from coding_agent.agents.coding.prompt import PromptBuilder
from coding_agent.agents.coding.state import CodingAgentState
from coding_agent.tools.contracts import ToolCall, ToolExecutionContext
from coding_agent.tools.executor import ToolExecutor
from coding_agent.workspace.context import WorkspaceContext


class CodingAgentNodes:
    def __init__(
        self,
        *,
        model: BaseChatModel,
        tool_model: Runnable[Any, AIMessage],
        prompt_builder: PromptBuilder,
        tool_executor: ToolExecutor,
        allowed_tools: set[str] | None = None,
    ) -> None:
        self.model = model
        self.tool_model = tool_model
        self.prompt_builder = prompt_builder
        self.tool_executor = tool_executor
        self.allowed_tools = allowed_tools

    def call_model(self, state: CodingAgentState) -> dict[str, Any]:
        response = self.tool_model.invoke(self.prompt_builder.build(state))
        termination_reason = "completed" if not response.tool_calls else None
        return {"messages": [response], "termination_reason": termination_reason}

    def call_tools(self, state: CodingAgentState) -> dict[str, Any]:
        message = self._last_tool_call_message(state)
        context = ToolExecutionContext(
            workspace=WorkspaceContext.from_path(state["workspace"]),
            run_id=state.get("run_id", "unknown"),
        )
        results: list[ToolMessage] = []
        for raw_call in message.tool_calls:
            if (
                self.allowed_tools is not None
                and self.tool_executor.registry.get(raw_call["name"]) is not None
                and raw_call["name"] not in self.allowed_tools
            ):
                results.append(
                    ToolMessage(
                        content="[tool_not_allowed] Tool is unavailable in this conversation mode.",
                        tool_call_id=raw_call["id"],
                        name=raw_call["name"],
                        status="error",
                        artifact={"error_code": "tool_not_allowed", "truncated": False},
                    )
                )
                continue
            call = ToolCall(
                call_id=raw_call["id"],
                name=raw_call["name"],
                arguments=raw_call.get("args", {}),
            )
            result = self.tool_executor.execute(call, context)
            results.append(
                ToolMessage(
                    content=result.content,
                    tool_call_id=result.call_id,
                    name=result.name,
                    status="error" if result.is_error else "success",
                    artifact={
                        "error_code": result.error_code,
                        "truncated": result.truncated,
                        "duration_ms": result.duration_ms,
                        "policy_decision": result.policy_decision,
                        "approved": result.approved,
                        **(result.metadata or {}),
                    },
                )
            )
        return {
            "messages": results,
            "tool_rounds": state.get("tool_rounds", 0) + 1,
            "termination_reason": None,
        }

    def resolve_tool_limit(self, state: CodingAgentState) -> dict[str, Any]:
        message = self._last_tool_call_message(state)
        results = [
            ToolMessage(
                content="[tool_round_limit] Tool call was not executed because the tool budget is exhausted.",
                tool_call_id=raw_call["id"],
                name=raw_call["name"],
                status="error",
                artifact={"error_code": "tool_round_limit", "truncated": False},
            )
            for raw_call in message.tool_calls
        ]
        return {"messages": results, "termination_reason": "max_tool_rounds"}

    def call_final_model(self, state: CodingAgentState) -> dict[str, Any]:
        response = self.model.invoke(self.prompt_builder.build(state, force_final=True))
        if response.tool_calls:
            response = AIMessage(content=response.content or "The tool budget is exhausted.")
        return {"messages": [response], "termination_reason": "max_tool_rounds"}

    @staticmethod
    def _last_tool_call_message(state: CodingAgentState) -> AIMessage:
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], AIMessage):
            raise RuntimeError("tool node requires a preceding AIMessage")
        return messages[-1]
