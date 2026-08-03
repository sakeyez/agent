"""Compatibility import for the coding agent state."""

from coding_agent.agents.coding.state import CodingAgentState
from coding_agent.agents.coding.tasks import StepStatus, TaskPlan, TaskStatus, VerificationStatus

AgentState = CodingAgentState

__all__ = [
    "AgentState",
    "CodingAgentState",
    "StepStatus",
    "TaskPlan",
    "TaskStatus",
    "VerificationStatus",
]
