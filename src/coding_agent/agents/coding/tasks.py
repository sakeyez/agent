"""Serializable task planning and verification domain types."""

from __future__ import annotations

from enum import Enum
from typing import Literal, NotRequired, TypedDict
from uuid import uuid4


class TaskStatus(str, Enum):
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    CORRECTING = "correcting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class VerificationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


TaskChangeScope = Literal["code", "docs", "none"]
TaskStepOrigin = Literal["initial", "correction"]


class TaskStep(TypedDict):
    id: str
    title: str
    status: str
    origin: TaskStepOrigin
    correction_attempt: int
    summary: NotRequired[str | None]


class VerificationEvidence(TypedDict):
    tool: str
    status: Literal["success", "error"]
    summary: str
    error_code: str | None
    exit_code: int | None
    validation_command: bool
    diff_review: bool


class VerificationAttempt(TypedDict):
    number: int
    status: str
    evidence: list[VerificationEvidence]
    summary: str | None
    failure_kind: str | None


class TaskPlan(TypedDict):
    id: str
    objective: str
    status: str
    change_scope: TaskChangeScope
    steps: list[TaskStep]
    current_step_id: str | None
    verification_status: str
    verification_attempts: list[VerificationAttempt]
    correction_attempts: int
    max_correction_attempts: int
    phase_tool_rounds: int
    total_tool_rounds: int
    max_phase_tool_rounds: int
    max_task_tool_rounds: int
    final_summary: str | None


TERMINAL_TASK_STATUSES = {
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
}


def create_planning_task(
    objective: str,
    *,
    max_correction_attempts: int,
    max_phase_tool_rounds: int,
    max_task_tool_rounds: int,
) -> TaskPlan:
    return {
        "id": uuid4().hex,
        "objective": objective,
        "status": TaskStatus.PLANNING.value,
        "change_scope": "none",
        "steps": [],
        "current_step_id": None,
        "verification_status": VerificationStatus.PENDING.value,
        "verification_attempts": [],
        "correction_attempts": 0,
        "max_correction_attempts": max_correction_attempts,
        "phase_tool_rounds": 0,
        "total_tool_rounds": 0,
        "max_phase_tool_rounds": max_phase_tool_rounds,
        "max_task_tool_rounds": max_task_tool_rounds,
        "final_summary": None,
    }


def create_steps(
    titles: list[str],
    *,
    origin: TaskStepOrigin = "initial",
    correction_attempt: int = 0,
    offset: int = 0,
) -> list[TaskStep]:
    return [
        {
            "id": f"step-{offset + index}",
            "title": title,
            "status": StepStatus.PENDING.value,
            "origin": origin,
            "correction_attempt": correction_attempt,
            "summary": None,
        }
        for index, title in enumerate(titles, start=1)
    ]


def is_task_terminal(task: TaskPlan | None) -> bool:
    return task is None or task.get("status") in TERMINAL_TASK_STATUSES


__all__ = [
    "StepStatus",
    "TaskChangeScope",
    "TaskPlan",
    "TaskStatus",
    "TaskStep",
    "TERMINAL_TASK_STATUSES",
    "VerificationAttempt",
    "VerificationEvidence",
    "VerificationStatus",
    "create_planning_task",
    "create_steps",
    "is_task_terminal",
]
