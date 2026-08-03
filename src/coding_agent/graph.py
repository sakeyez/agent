"""Compatibility import for the workspace-aware coding agent graph."""

from coding_agent.agents.coding.graph import (
    DEFAULT_MAX_CORRECTION_ATTEMPTS,
    DEFAULT_MAX_TASK_TOOL_ROUNDS,
    DEFAULT_MAX_TOOL_ROUNDS,
    create_agent_graph,
)

__all__ = [
    "DEFAULT_MAX_CORRECTION_ATTEMPTS",
    "DEFAULT_MAX_TASK_TOOL_ROUNDS",
    "DEFAULT_MAX_TOOL_ROUNDS",
    "create_agent_graph",
]
