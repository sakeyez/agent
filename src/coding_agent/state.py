"""Compatibility import for the coding agent state."""

from coding_agent.agents.coding.state import CodingAgentState, LongTermMemory
from coding_agent.agents.coding.tasks import StepStatus, TaskPlan, TaskStatus, VerificationStatus

AgentState = CodingAgentState

__all__ = [
    "AgentState",
    "CodingAgentState",
    "LongTermMemory",
    "StepStatus",
    "TaskPlan",
    "TaskStatus",
    "VerificationStatus",
]
