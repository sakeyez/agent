"""Persistent chat and task state-machine graph construction."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from coding_agent.agents.coding.context import (
    DEFAULT_CONTEXT_KEEP_RECENT_TURNS,
    DEFAULT_CONTEXT_MAX_CHARS,
    DEFAULT_MEMORY_SUMMARY_MAX_CHARS,
    ContextCompactor,
    ContextManager,
    ModelContextCompactor,
)
from coding_agent.agents.coding.nodes import CodingAgentNodes
from coding_agent.agents.coding.planner import ModelTaskPlanner, TaskPlanner
from coding_agent.agents.coding.prompt import PromptBuilder
from coding_agent.agents.coding.routing import route_after_model
from coding_agent.agents.coding.state import CodingAgentState
from coding_agent.agents.coding.task_nodes import (
    STEP_CONTROL_SCHEMA,
    VERIFICATION_CONTROL_SCHEMA,
    TaskAgentNodes,
)
from coding_agent.agents.coding.tasks import TERMINAL_TASK_STATUSES
from coding_agent.tools.builtin import create_coding_tool_registry
from coding_agent.tools.executor import ToolExecutor
from coding_agent.tools.registry import ToolRegistry

DEFAULT_MAX_TOOL_ROUNDS = 8
DEFAULT_MAX_TASK_TOOL_ROUNDS = 24
DEFAULT_MAX_CORRECTION_ATTEMPTS = 2

_CHAT_TOOLS = {"list_files", "read_file", "search_text", "git_diff"}
_VERIFICATION_TOOLS = {
    "list_files",
    "read_file",
    "search_text",
    "run_command",
    "git_diff",
}


def _bind_schemas(
    model: BaseChatModel, schemas: list[dict[str, Any]]
) -> Runnable[Any, Any]:
    if not schemas:
        return model
    try:
        return model.bind_tools(schemas)
    except NotImplementedError:
        return model


def _action(state: CodingAgentState) -> str:
    return state.get("control_action") or "final"


def _route_phase_model(state: CodingAgentState, *, verification: bool) -> str:
    task = state.get("task")
    if task is None or task.get("status") in TERMINAL_TASK_STATUSES:
        return "final"
    messages = state.get("messages", [])
    if not messages or not isinstance(messages[-1], AIMessage):
        return "protocol"
    if not messages[-1].tool_calls:
        return "protocol"
    return "tools"


def create_agent_graph(
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver | None = None,
    prompt_builder: PromptBuilder | None = None,
    *,
    tool_registry: ToolRegistry | None = None,
    tool_executor: ToolExecutor | None = None,
    task_planner: TaskPlanner | None = None,
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    max_phase_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    max_task_tool_rounds: int = DEFAULT_MAX_TASK_TOOL_ROUNDS,
    max_correction_attempts: int = DEFAULT_MAX_CORRECTION_ATTEMPTS,
    context_compactor: ContextCompactor | None = None,
    context_max_chars: int = DEFAULT_CONTEXT_MAX_CHARS,
    context_keep_recent_turns: int = DEFAULT_CONTEXT_KEEP_RECENT_TURNS,
    memory_summary_max_chars: int = DEFAULT_MEMORY_SUMMARY_MAX_CHARS,
) -> CompiledStateGraph:
    """Compile the bounded chat loop and persistent task lifecycle."""

    if min(max_tool_rounds, max_phase_tool_rounds, max_task_tool_rounds) <= 0:
        raise ValueError("tool round limits must be positive")
    if max_correction_attempts < 0:
        raise ValueError("max_correction_attempts cannot be negative")
    context_manager = ContextManager(
        context_compactor
        or ModelContextCompactor(model, summary_max_chars=memory_summary_max_chars),
        max_chars=context_max_chars,
        keep_recent_turns=context_keep_recent_turns,
    )
    registry = tool_registry or (
        tool_executor.registry if tool_executor is not None else create_coding_tool_registry()
    )
    executor = tool_executor or ToolExecutor(registry)
    if executor.registry is not registry:
        raise ValueError("tool_executor and tool_registry must use the same registry")

    prompts = prompt_builder or PromptBuilder()
    chat_nodes = CodingAgentNodes(
        model=model,
        tool_model=_bind_schemas(model, registry.model_schemas(_CHAT_TOOLS)),
        prompt_builder=prompts,
        tool_executor=executor,
        allowed_tools=_CHAT_TOOLS,
    )
    task_nodes = TaskAgentNodes(
        model=model,
        task_model=_bind_schemas(
            model,
            [*registry.model_schemas(), STEP_CONTROL_SCHEMA],
        ),
        verification_model=_bind_schemas(
            model,
            [*registry.model_schemas(_VERIFICATION_TOOLS), VERIFICATION_CONTROL_SCHEMA],
        ),
        final_model=model,
        prompt_builder=prompts,
        tool_executor=executor,
        planner=task_planner or ModelTaskPlanner(model),
        max_phase_tool_rounds=max_phase_tool_rounds,
        max_task_tool_rounds=max_task_tool_rounds,
        max_correction_attempts=max_correction_attempts,
    )

    builder = StateGraph(CodingAgentState)
    builder.add_node("compact_context", context_manager.compact)
    builder.add_node("intake", task_nodes.intake)
    builder.add_node("chat_model", chat_nodes.call_model)
    builder.add_node("chat_tools", chat_nodes.call_tools)
    builder.add_node("chat_limit", chat_nodes.resolve_tool_limit)
    builder.add_node("chat_final", chat_nodes.call_final_model)
    builder.add_node("plan_task", task_nodes.plan_task)
    builder.add_node("prepare_step", task_nodes.prepare_step)
    builder.add_node("task_model", task_nodes.call_task_model)
    builder.add_node("task_tools", task_nodes.call_task_tools)
    builder.add_node("task_protocol", task_nodes.protocol_repair)
    builder.add_node("start_verification", task_nodes.start_verification)
    builder.add_node("verification_model", task_nodes.call_verification_model)
    builder.add_node("verification_tools", task_nodes.call_verification_tools)
    builder.add_node("verification_protocol", task_nodes.protocol_repair)
    builder.add_node("prepare_correction", task_nodes.prepare_correction)
    builder.add_node("task_final", task_nodes.final_task)

    builder.add_edge(START, "compact_context")
    builder.add_edge("compact_context", "intake")
    builder.add_conditional_edges(
        "intake",
        _action,
        {"chat": "chat_model", "plan": "plan_task"},
    )
    builder.add_conditional_edges(
        "chat_model",
        lambda state: route_after_model(state, max_tool_rounds=max_tool_rounds),
        {"tools": "chat_tools", "tool_limit": "chat_limit", "__end__": END},
    )
    builder.add_edge("chat_tools", "chat_model")
    builder.add_edge("chat_limit", "chat_final")
    builder.add_edge("chat_final", END)
    builder.add_conditional_edges(
        "plan_task",
        _action,
        {
            "chat": "chat_model",
            "prepare_step": "prepare_step",
            "final": "task_final",
        },
    )
    builder.add_conditional_edges(
        "prepare_step",
        _action,
        {
            "task_model": "task_model",
            "verify": "start_verification",
            "final": "task_final",
        },
    )
    builder.add_conditional_edges(
        "task_model",
        lambda state: _route_phase_model(state, verification=False),
        {"tools": "task_tools", "protocol": "task_protocol", "final": "task_final"},
    )
    builder.add_conditional_edges(
        "task_tools",
        _action,
        {
            "model": "task_model",
            "prepare_step": "prepare_step",
            "final": "task_final",
        },
    )
    builder.add_conditional_edges(
        "task_protocol",
        _action,
        {"task_model": "task_model", "verification_model": "verification_model", "final": "task_final"},
    )
    builder.add_edge("start_verification", "verification_model")
    builder.add_conditional_edges(
        "verification_model",
        lambda state: _route_phase_model(state, verification=True),
        {
            "tools": "verification_tools",
            "protocol": "verification_protocol",
            "final": "task_final",
        },
    )
    builder.add_conditional_edges(
        "verification_tools",
        _action,
        {
            "model": "verification_model",
            "correction": "prepare_correction",
            "final": "task_final",
        },
    )
    builder.add_conditional_edges(
        "verification_protocol",
        _action,
        {"verification_model": "verification_model", "task_model": "task_model", "final": "task_final"},
    )
    builder.add_conditional_edges(
        "prepare_correction",
        _action,
        {"prepare_step": "prepare_step", "final": "task_final"},
    )
    builder.add_edge("task_final", END)
    return builder.compile(checkpointer=checkpointer)


__all__ = [
    "DEFAULT_MAX_CORRECTION_ATTEMPTS",
    "DEFAULT_MAX_TASK_TOOL_ROUNDS",
    "DEFAULT_MAX_TOOL_ROUNDS",
    "create_agent_graph",
]
