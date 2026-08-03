"""Structured model-backed task planning with injectable test seams."""

from __future__ import annotations

import re
from typing import Any, Literal, Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class PlanningError(RuntimeError):
    """Raised when the model cannot produce a valid task decision."""


class PlanningDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["chat", "task"]
    objective: str = Field(min_length=1, max_length=500)
    steps: list[str] = Field(default_factory=list, max_length=8)
    change_scope: Literal["code", "docs", "none"] = "none"

    @model_validator(mode="after")
    def task_requires_steps(self) -> "PlanningDecision":
        if self.mode == "task" and not self.steps:
            raise ValueError("task plan contains no steps")
        return self


class CorrectionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[str] = Field(min_length=1, max_length=6)


class TaskPlanner(Protocol):
    def plan(self, request: str, workspace: str) -> PlanningDecision: ...

    def plan_correction(
        self, objective: str, failure_summary: str, workspace: str
    ) -> CorrectionDecision: ...


_TASK_HINT = re.compile(
    r"(?:\b(?:add|build|change|create|delete|fix|implement|migrate|refactor|remove|rename|"
    r"update|write)\b|增加|新增|修改|修复|实现|完成|重构|迁移|删除|创建|编写|验证|纠错)",
    re.IGNORECASE,
)


def looks_like_task(request: str) -> bool:
    """Cheaply keep obvious conversation out of the structured planning call."""

    return bool(_TASK_HINT.search(request)) or "\n- " in request or "\n1." in request


def _tool_schema(name: str, description: str, model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": model.model_json_schema(),
        },
    }


class ModelTaskPlanner:
    def __init__(self, model: BaseChatModel, *, max_attempts: int = 2) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self.model = model
        self.max_attempts = max_attempts

    def plan(self, request: str, workspace: str) -> PlanningDecision:
        prompt = (
            "Classify the request. Use task mode for workspace changes or multi-step work; "
            "use chat mode for ordinary questions. For task mode provide 1-8 independently "
            "executable steps. Set change_scope to code for source, tests, configuration, or "
            "build changes; docs for documentation-only changes; otherwise none.\n\n"
            f"Workspace: {workspace}\nRequest: {request}"
        )
        result = self._call(
            PlanningDecision,
            "submit_task_plan",
            "Return the validated task classification and plan.",
            prompt,
        )
        decision = PlanningDecision.model_validate(result)
        return decision

    def plan_correction(
        self, objective: str, failure_summary: str, workspace: str
    ) -> CorrectionDecision:
        prompt = (
            "Create focused correction steps for a failed validation. Do not repeat completed "
            "work. Base every step on the supplied evidence.\n\n"
            f"Workspace: {workspace}\nObjective: {objective}\nFailure evidence:\n{failure_summary}"
        )
        result = self._call(
            CorrectionDecision,
            "submit_correction_plan",
            "Return focused steps that address the validation failure.",
            prompt,
        )
        return CorrectionDecision.model_validate(result)

    def _call(
        self,
        schema: type[BaseModel],
        tool_name: str,
        description: str,
        prompt: str,
    ) -> dict[str, Any]:
        bound = self.model.bind_tools(
            [_tool_schema(tool_name, description, schema)],
            tool_choice=tool_name,
        )
        error_detail = ""
        for _attempt in range(self.max_attempts):
            messages = [
                SystemMessage(
                    content=(
                        "You are a task controller. Respond only by calling the required "
                        "structured tool. Do not perform the task."
                    )
                ),
                HumanMessage(content=prompt + error_detail),
            ]
            response = bound.invoke(messages)
            if isinstance(response, AIMessage):
                for call in response.tool_calls:
                    if call.get("name") != tool_name:
                        continue
                    try:
                        return schema.model_validate(call.get("args", {})).model_dump()
                    except ValidationError as error:
                        error_detail = f"\nPrevious output was invalid: {error}"
                        break
            error_detail = "\nPrevious output did not call the required tool."
        raise PlanningError(f"model did not produce a valid {tool_name} call")


__all__ = [
    "CorrectionDecision",
    "ModelTaskPlanner",
    "PlanningDecision",
    "PlanningError",
    "TaskPlanner",
    "looks_like_task",
]
