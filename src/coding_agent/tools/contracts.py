"""Provider-neutral tool request, result, and error contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel

from coding_agent.workspace.context import WorkspaceContext


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    workspace: WorkspaceContext
    run_id: str = "unknown"


class ToolEffect(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


@dataclass(frozen=True, slots=True)
class ToolHandlerOutput:
    content: str
    is_error: bool = False
    error_code: str | None = None
    metadata: dict[str, Any] | None = None


ToolHandler = Callable[[BaseModel, ToolExecutionContext], str | ToolHandlerOutput]
ToolSummaryBuilder = Callable[[BaseModel, ToolExecutionContext], str]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    args_schema: type[BaseModel]
    handler: ToolHandler
    effect: ToolEffect = ToolEffect.READ
    timeout_seconds: float | None = None
    summary_builder: ToolSummaryBuilder | None = None
    parameters_schema: dict[str, Any] | None = None

    def model_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema or self.args_schema.model_json_schema(),
            },
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    call_id: str
    name: str
    content: str
    is_error: bool = False
    truncated: bool = False
    error_code: str | None = None
    duration_ms: int = 0
    policy_decision: str | None = None
    approved: bool | None = None
    metadata: dict[str, Any] | None = None
