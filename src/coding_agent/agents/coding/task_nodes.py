"""Nodes for persistent planning, execution, verification, and correction."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from coding_agent.agents.coding.planner import PlanningError, TaskPlanner, looks_like_task
from coding_agent.agents.coding.prompt import PromptBuilder
from coding_agent.agents.coding.state import CodingAgentState
from coding_agent.agents.coding.tasks import (
    StepStatus,
    TaskPlan,
    TaskStatus,
    VerificationEvidence,
    VerificationStatus,
    create_planning_task,
    create_steps,
)
from coding_agent.tools.contracts import ToolCall, ToolExecutionContext
from coding_agent.tools.executor import ToolExecutor
from coding_agent.workspace.context import WorkspaceContext

REPORT_STEP_TOOL = "report_step_result"
REPORT_VERIFICATION_TOOL = "report_verification"
VERIFICATION_ALLOWED_TOOLS = {
    "list_files",
    "read_file",
    "search_text",
    "run_command",
    "git_diff",
}


class ReportStepResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1)
    outcome: Literal["completed", "failed"]
    summary: str = Field(min_length=1, max_length=1000)


class ReportVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["passed", "failed"]
    summary: str = Field(min_length=1, max_length=1500)


def control_schema(name: str, description: str, model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": model.model_json_schema(),
        },
    }


STEP_CONTROL_SCHEMA = control_schema(
    REPORT_STEP_TOOL,
    "Report the outcome of the current persisted task step.",
    ReportStepResult,
)
VERIFICATION_CONTROL_SCHEMA = control_schema(
    REPORT_VERIFICATION_TOOL,
    "Report the verification outcome after collecting required evidence.",
    ReportVerification,
)


class TaskAgentNodes:
    def __init__(
        self,
        *,
        model: BaseChatModel,
        task_model: Runnable[Any, AIMessage],
        verification_model: Runnable[Any, AIMessage],
        final_model: BaseChatModel,
        prompt_builder: PromptBuilder,
        tool_executor: ToolExecutor,
        planner: TaskPlanner,
        max_phase_tool_rounds: int,
        max_task_tool_rounds: int,
        max_correction_attempts: int,
    ) -> None:
        self.model = model
        self.task_model = task_model
        self.verification_model = verification_model
        self.final_model = final_model
        self.prompt_builder = prompt_builder
        self.tool_executor = tool_executor
        self.planner = planner
        self.max_phase_tool_rounds = max_phase_tool_rounds
        self.max_task_tool_rounds = max_task_tool_rounds
        self.max_correction_attempts = max_correction_attempts

    def intake(self, state: CodingAgentState) -> dict[str, Any]:
        request = state.get("request") or self._latest_human_text(state)
        if not request or not looks_like_task(request):
            return {
                "task_mode": False,
                "request": request,
                "tool_rounds": 0,
                "protocol_retries": 0,
                "control_action": "chat",
            }
        task = create_planning_task(
            request,
            max_correction_attempts=self.max_correction_attempts,
            max_phase_tool_rounds=self.max_phase_tool_rounds,
            max_task_tool_rounds=self.max_task_tool_rounds,
        )
        return {
            "task_mode": True,
            "task": task,
            "request": request,
            "tool_rounds": 0,
            "protocol_retries": 0,
            "control_action": "plan",
            "termination_reason": None,
        }

    def plan_task(self, state: CodingAgentState) -> dict[str, Any]:
        task = self._task(state)
        if self._cancelled(task):
            return {"control_action": "final"}
        try:
            decision = self.planner.plan(task["objective"], state["workspace"])
        except (PlanningError, ValidationError, ValueError, NotImplementedError) as error:
            task["status"] = TaskStatus.FAILED.value
            task["final_summary"] = f"Task planning failed: {str(error)[:500]}"
            return {
                "task": task,
                "control_action": "final",
                "termination_reason": "planning_failed",
            }
        if decision.mode == "chat":
            return {"task_mode": False, "task": None, "control_action": "chat"}
        task["objective"] = decision.objective
        task["change_scope"] = decision.change_scope
        task["steps"] = create_steps(decision.steps)
        task["status"] = TaskStatus.EXECUTING.value
        task["phase_tool_rounds"] = 0
        return {"task": task, "control_action": "prepare_step"}

    def prepare_step(self, state: CodingAgentState) -> dict[str, Any]:
        task = self._task(state)
        if self._cancelled(task):
            return {"control_action": "final"}
        pending = next(
            (step for step in task["steps"] if step["status"] == StepStatus.PENDING.value),
            None,
        )
        if pending is None:
            task["current_step_id"] = None
            return {"task": task, "control_action": "verify"}
        pending["status"] = StepStatus.IN_PROGRESS.value
        task["current_step_id"] = pending["id"]
        task["status"] = (
            TaskStatus.CORRECTING.value
            if pending["origin"] == "correction"
            else TaskStatus.EXECUTING.value
        )
        return {
            "task": task,
            "protocol_retries": 0,
            "control_action": "task_model",
        }

    def call_task_model(self, state: CodingAgentState) -> dict[str, Any]:
        task = self._task(state)
        if self._cancelled(task):
            return {"control_action": "final"}
        response = self.task_model.invoke(self.prompt_builder.build_task(state))
        return {"messages": [response], "control_action": "route_task_model"}

    def call_verification_model(self, state: CodingAgentState) -> dict[str, Any]:
        task = self._task(state)
        if self._cancelled(task):
            return {"control_action": "final"}
        response = self.verification_model.invoke(
            self.prompt_builder.build_task(state, verification=True)
        )
        return {"messages": [response], "control_action": "route_verification_model"}

    def call_task_tools(self, state: CodingAgentState) -> dict[str, Any]:
        return self._call_tools(state, verification=False)

    def call_verification_tools(self, state: CodingAgentState) -> dict[str, Any]:
        return self._call_tools(state, verification=True)

    def _call_tools(self, state: CodingAgentState, *, verification: bool) -> dict[str, Any]:
        task = self._task(state)
        if self._cancelled(task):
            return {"control_action": "final"}
        message = self._last_ai_message(state)
        control_name = REPORT_VERIFICATION_TOOL if verification else REPORT_STEP_TOOL
        controls = [call for call in message.tool_calls if call["name"] == control_name]
        externals = [call for call in message.tool_calls if call["name"] != control_name]
        if controls:
            if len(controls) != 1 or externals:
                results = [
                    ToolMessage(
                        content="[mixed_control_call] State control must be the only tool call.",
                        tool_call_id=call["id"],
                        name=call["name"],
                        status="error",
                    )
                    for call in message.tool_calls
                ]
                return {"messages": results, "control_action": "model"}
            return (
                self._report_verification(task, controls[0])
                if verification
                else self._report_step(task, controls[0])
            )

        if self._budget_exhausted(task):
            return self._fail_budget(task, message)

        context = ToolExecutionContext(
            workspace=WorkspaceContext.from_path(state["workspace"]),
            run_id=state.get("run_id", "unknown"),
        )
        results: list[ToolMessage] = []
        evidence: list[VerificationEvidence] = []
        for raw_call in externals:
            if verification and raw_call["name"] not in VERIFICATION_ALLOWED_TOOLS:
                results.append(
                    ToolMessage(
                        content="[tool_not_allowed] Mutating tools are unavailable during verification.",
                        tool_call_id=raw_call["id"],
                        name=raw_call["name"],
                        status="error",
                        artifact={"error_code": "tool_not_allowed", "truncated": False},
                    )
                )
                evidence.append(
                    {
                        "tool": raw_call["name"],
                        "status": "error",
                        "summary": "Tool is unavailable during verification.",
                        "error_code": "tool_not_allowed",
                        "exit_code": None,
                        "validation_command": False,
                        "diff_review": False,
                    }
                )
                continue
            result = self.tool_executor.execute(
                ToolCall(
                    call_id=raw_call["id"],
                    name=raw_call["name"],
                    arguments=raw_call.get("args", {}),
                ),
                context,
            )
            artifact = {
                "error_code": result.error_code,
                "truncated": result.truncated,
                "duration_ms": result.duration_ms,
                "policy_decision": result.policy_decision,
                "approved": result.approved,
                **(result.metadata or {}),
            }
            results.append(
                ToolMessage(
                    content=result.content,
                    tool_call_id=result.call_id,
                    name=result.name,
                    status="error" if result.is_error else "success",
                    artifact=artifact,
                )
            )
            if verification:
                evidence.append(
                    {
                        "tool": result.name,
                        "status": "error" if result.is_error else "success",
                        "summary": result.content.splitlines()[0][:300],
                        "error_code": result.error_code,
                        "exit_code": artifact.get("exit_code"),
                        "validation_command": artifact.get("operation_kind") == "validation",
                        "diff_review": result.name == "git_diff" and not result.is_error,
                    }
                )
        task["phase_tool_rounds"] += 1
        task["total_tool_rounds"] += 1
        if verification and task["verification_attempts"]:
            task["verification_attempts"][-1]["evidence"].extend(evidence)
        return {
            "messages": results,
            "task": task,
            "control_action": "model",
        }

    def protocol_repair(self, state: CodingAgentState) -> dict[str, Any]:
        task = self._task(state)
        retries = state.get("protocol_retries", 0) + 1
        if retries > 2:
            return self._fail_task(task, "Model did not report a structured phase result.", "protocol_error")
        verification = task["status"] == TaskStatus.VERIFYING.value
        tool_name = REPORT_VERIFICATION_TOOL if verification else REPORT_STEP_TOOL
        return {
            "messages": [
                HumanMessage(
                    content=f"Use {tool_name} as the only tool call to report this phase outcome."
                )
            ],
            "protocol_retries": retries,
            "control_action": "verification_model" if verification else "task_model",
        }

    def start_verification(self, state: CodingAgentState) -> dict[str, Any]:
        task = self._task(state)
        if self._cancelled(task):
            return {"control_action": "final"}
        task["status"] = TaskStatus.VERIFYING.value
        task["verification_status"] = VerificationStatus.RUNNING.value
        task["phase_tool_rounds"] = 0
        task["verification_attempts"].append(
            {
                "number": len(task["verification_attempts"]) + 1,
                "status": VerificationStatus.RUNNING.value,
                "evidence": [],
                "summary": None,
                "failure_kind": None,
            }
        )
        return {
            "task": task,
            "protocol_retries": 0,
            "control_action": "verification_model",
        }

    def prepare_correction(self, state: CodingAgentState) -> dict[str, Any]:
        task = self._task(state)
        if self._cancelled(task):
            return {"control_action": "final"}
        attempt = task["verification_attempts"][-1]
        failure_summary = self._evidence_summary(attempt["evidence"], attempt.get("summary"))
        try:
            decision = self.planner.plan_correction(
                task["objective"], failure_summary, state["workspace"]
            )
        except (PlanningError, ValidationError, ValueError, NotImplementedError) as error:
            return self._fail_task(
                task,
                f"Correction planning failed: {str(error)[:500]}",
                "correction_planning_failed",
            )
        task["correction_attempts"] += 1
        task["steps"].extend(
            create_steps(
                decision.steps,
                origin="correction",
                correction_attempt=task["correction_attempts"],
                offset=len(task["steps"]),
            )
        )
        task["status"] = TaskStatus.CORRECTING.value
        task["phase_tool_rounds"] = 0
        return {"task": task, "control_action": "prepare_step"}

    def fail_budget(self, state: CodingAgentState) -> dict[str, Any]:
        return self._fail_budget(self._task(state), self._last_ai_message(state))

    def final_task(self, state: CodingAgentState) -> dict[str, Any]:
        task = self._task(state)
        if task["status"] == TaskStatus.CANCELLED.value:
            response = AIMessage(content="Task cancelled. The persisted plan and evidence were retained.")
        else:
            response = self.final_model.invoke(self.prompt_builder.build_task_final(state))
            if response.tool_calls:
                response = AIMessage(content=str(response.content) or task.get("final_summary") or task["status"])
        return {
            "messages": [response],
            "termination_reason": task["status"],
            "control_action": "end",
        }

    def _report_step(self, task: TaskPlan, raw_call: dict[str, Any]) -> dict[str, Any]:
        try:
            report = ReportStepResult.model_validate(raw_call.get("args", {}))
        except ValidationError as error:
            return self._control_error(raw_call, "invalid_step_report", str(error))
        current = next(
            (step for step in task["steps"] if step["id"] == task.get("current_step_id")),
            None,
        )
        if current is None or report.step_id != current["id"]:
            return self._control_error(raw_call, "wrong_step", "Report must target the current step.")
        current["summary"] = report.summary
        current["status"] = (
            StepStatus.COMPLETED.value
            if report.outcome == "completed"
            else StepStatus.FAILED.value
        )
        message = ToolMessage(
            content=f"Step {report.step_id} recorded as {report.outcome}.",
            tool_call_id=raw_call["id"],
            name=REPORT_STEP_TOOL,
            status="success",
        )
        if report.outcome == "failed":
            task["status"] = TaskStatus.FAILED.value
            task["final_summary"] = report.summary
            return {
                "messages": [message],
                "task": task,
                "control_action": "final",
                "termination_reason": "step_failed",
            }
        task["current_step_id"] = None
        return {
            "messages": [message],
            "task": task,
            "protocol_retries": 0,
            "control_action": "prepare_step",
        }

    def _report_verification(
        self, task: TaskPlan, raw_call: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            report = ReportVerification.model_validate(raw_call.get("args", {}))
        except ValidationError as error:
            return self._control_error(raw_call, "invalid_verification_report", str(error))
        attempt = task["verification_attempts"][-1]
        evidence = attempt["evidence"]
        diff_ok = any(item["diff_review"] and item["status"] == "success" for item in evidence)
        validations = [item for item in evidence if item["validation_command"]]
        validation_ok = bool(validations) and all(item["status"] == "success" for item in validations)
        evidence_ok = diff_ok and (task["change_scope"] == "docs" or validation_ok)
        passed = report.outcome == "passed" and evidence_ok
        attempt["summary"] = report.summary
        message_status: Literal["success", "error"] = "success" if passed else "error"
        if passed:
            attempt["status"] = VerificationStatus.PASSED.value
            task["verification_status"] = VerificationStatus.PASSED.value
            task["status"] = TaskStatus.COMPLETED.value
            task["final_summary"] = report.summary
            content = "Verification passed and required evidence is present."
            action = "final"
        else:
            failure_kind = self._verification_failure_kind(evidence, diff_ok, validations)
            attempt["status"] = VerificationStatus.FAILED.value
            attempt["failure_kind"] = failure_kind
            task["verification_status"] = VerificationStatus.FAILED.value
            correctable = failure_kind == "validation_failed"
            if correctable and task["correction_attempts"] < task["max_correction_attempts"]:
                task["status"] = TaskStatus.CORRECTING.value
                action = "correction"
            else:
                task["status"] = TaskStatus.FAILED.value
                task["final_summary"] = report.summary
                action = "final"
            content = f"Verification failed ({failure_kind})."
        return {
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id=raw_call["id"],
                    name=REPORT_VERIFICATION_TOOL,
                    status=message_status,
                )
            ],
            "task": task,
            "protocol_retries": 0,
            "control_action": action,
        }

    @staticmethod
    def _verification_failure_kind(
        evidence: list[VerificationEvidence],
        diff_ok: bool,
        validations: list[VerificationEvidence],
    ) -> str:
        if any(item["error_code"] == "nonzero_exit" for item in validations):
            return "validation_failed"
        terminal_codes = [item["error_code"] for item in evidence if item["status"] == "error"]
        if terminal_codes:
            return next((code for code in terminal_codes if code), "tool_error")
        if not validations:
            return "validation_unavailable"
        if not diff_ok:
            return "diff_review_missing"
        return "evidence_rejected"

    def _fail_budget(self, task: TaskPlan, message: AIMessage) -> dict[str, Any]:
        results = [
            ToolMessage(
                content="[task_tool_budget] Tool call was not executed because the task budget is exhausted.",
                tool_call_id=call["id"],
                name=call["name"],
                status="error",
            )
            for call in message.tool_calls
        ]
        failed = self._fail_task(task, "Task tool budget exhausted.", "task_tool_budget")
        failed["messages"] = results
        return failed

    def _fail_task(self, task: TaskPlan, summary: str, reason: str) -> dict[str, Any]:
        task["status"] = TaskStatus.FAILED.value
        task["final_summary"] = summary
        return {
            "task": task,
            "control_action": "final",
            "termination_reason": reason,
        }

    def _budget_exhausted(self, task: TaskPlan) -> bool:
        return (
            task["phase_tool_rounds"] >= task["max_phase_tool_rounds"]
            or task["total_tool_rounds"] >= task["max_task_tool_rounds"]
        )

    @staticmethod
    def _control_error(raw_call: dict[str, Any], code: str, detail: str) -> dict[str, Any]:
        return {
            "messages": [
                ToolMessage(
                    content=f"[{code}] {detail[:500]}",
                    tool_call_id=raw_call["id"],
                    name=raw_call["name"],
                    status="error",
                )
            ],
            "control_action": "model",
        }

    @staticmethod
    def _evidence_summary(evidence: list[VerificationEvidence], summary: str | None) -> str:
        lines = [summary or "Validation failed."]
        lines.extend(
            f"- {item['tool']}: {item['status']} {item['error_code'] or ''} {item['summary']}"
            for item in evidence
        )
        return "\n".join(lines)[:6000]

    @staticmethod
    def _task(state: CodingAgentState) -> TaskPlan:
        task = state.get("task")
        if task is None:
            raise RuntimeError("task node requires persisted task state")
        return deepcopy(task)

    @staticmethod
    def _cancelled(task: TaskPlan) -> bool:
        return task["status"] == TaskStatus.CANCELLED.value

    @staticmethod
    def _latest_human_text(state: CodingAgentState) -> str:
        for message in reversed(state.get("messages", [])):
            if isinstance(message, HumanMessage) and isinstance(message.content, str):
                return message.content
        return ""

    @staticmethod
    def _last_ai_message(state: CodingAgentState) -> AIMessage:
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], AIMessage):
            raise RuntimeError("tool node requires a preceding AIMessage")
        return messages[-1]


__all__ = [
    "REPORT_STEP_TOOL",
    "REPORT_VERIFICATION_TOOL",
    "ReportStepResult",
    "ReportVerification",
    "STEP_CONTROL_SCHEMA",
    "TaskAgentNodes",
    "VERIFICATION_CONTROL_SCHEMA",
    "VERIFICATION_ALLOWED_TOOLS",
]
