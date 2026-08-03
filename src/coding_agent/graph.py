"""Compatibility import for the workspace-aware coding agent graph."""

from coding_agent.agents.coding.graph import (
    DEFAULT_MAX_CORRECTION_ATTEMPTS,
    DEFAULT_MAX_TASK_TOOL_ROUNDS,
    DEFAULT_MAX_TOOL_ROUNDS,
    create_agent_graph,
)
from coding_agent.agents.coding.context import (
    DEFAULT_CONTEXT_KEEP_RECENT_TURNS,
    DEFAULT_CONTEXT_MAX_CHARS,
    DEFAULT_MEMORY_SUMMARY_MAX_CHARS,
)

__all__ = [
    "DEFAULT_MAX_CORRECTION_ATTEMPTS",
    "DEFAULT_MAX_TASK_TOOL_ROUNDS",
    "DEFAULT_MAX_TOOL_ROUNDS",
    "DEFAULT_CONTEXT_KEEP_RECENT_TURNS",
    "DEFAULT_CONTEXT_MAX_CHARS",
    "DEFAULT_MEMORY_SUMMARY_MAX_CHARS",
    "create_agent_graph",
]
