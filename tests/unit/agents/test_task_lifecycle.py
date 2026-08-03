from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, Field

from coding_agent.agents.coding.graph import create_agent_graph
from coding_agent.agents.coding.planner import CorrectionDecision, PlanningDecision
from coding_agent.tools.contracts import ToolDefinition, ToolEffect, ToolHandlerOutput
from coding_agent.tools.registry import ToolRegistry


class ScriptedModel(BaseChatModel):
    responses: list[AIMessage]
    index: int = 0

    @property
    def _llm_type(self) -> str:
        return "task-lifecycle-test"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, *args, **kwargs) -> ChatResult:
        response = self.responses[self.index]
        self.index += 1
        return ChatResult(generations=[ChatGeneration(message=response)])


class StaticPlanner:
    def __init__(self, *, scope: str = "code") -> None:
        self.scope = scope
        self.corrections = 0

    def plan(self, request: str, workspace: str) -> PlanningDecision:
        return PlanningDecision(
            mode="task",
            objective=request,
            steps=["implement the requested change"],
            change_scope=self.scope,
        )

    def plan_correction(
        self, objective: str, failure_summary: str, workspace: str
    ) -> CorrectionDecision:
        self.corrections += 1
        return CorrectionDecision(steps=["correct the validation failure"])


class CommandArgs(BaseModel):
    argv: list[str] = Field(min_length=1)
    cwd: str = "."


class EmptyArgs(BaseModel):
    pass


def _call(call_id: str, name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": name, "args": args}],
    )


def _step(call_id: str, step_id: str) -> AIMessage:
    return _call(
        call_id,
        "report_step_result",
        {"step_id": step_id, "outcome": "completed", "summary": "step complete"},
    )


def _verification(call_id: str, outcome: str) -> AIMessage:
    return _call(
        call_id,
        "report_verification",
        {"outcome": outcome, "summary": f"verification {outcome}"},
    )


def _input(tmp_path: Path, request: str = "implement a change") -> dict[str, Any]:
    return {
        "messages": [HumanMessage(content=request)],
        "request": request,
        "workspace": str(tmp_path),
        "tool_rounds": 0,
        "termination_reason": None,
    }


def test_documentation_task_completes_with_diff_evidence(tmp_path: Path) -> None:
    registry = ToolRegistry(
        [
            ToolDefinition(
                name="git_diff",
                description="inspect diff",
                args_schema=EmptyArgs,
                handler=lambda _args, _context: "diff reviewed",
            )
        ]
    )
    model = ScriptedModel(
        responses=[
            _step("step-done", "step-1"),
            _call("diff", "git_diff", {}),
            _verification("verified", "passed"),
            AIMessage(content="Task completed with diff review."),
        ]
    )

    result = create_agent_graph(
        model,
        task_planner=StaticPlanner(scope="docs"),
        tool_registry=registry,
    ).invoke(_input(tmp_path, "update documentation"))

    task = result["task"]
    assert task["status"] == "completed"
    assert task["steps"][0]["status"] == "completed"
    assert task["verification_status"] == "passed"
    assert task["verification_attempts"][0]["evidence"][0]["diff_review"] is True
    assert task["total_tool_rounds"] == 1
    assert result["termination_reason"] == "completed"


def test_validation_failure_adds_correction_and_reverifies(tmp_path: Path) -> None:
    command_calls = 0

    def run_command(_args, _context):
        nonlocal command_calls
        command_calls += 1
        if command_calls == 1:
            return ToolHandlerOutput(
                "Exit code: 1\nfailed test",
                is_error=True,
                error_code="nonzero_exit",
                metadata={"exit_code": 1},
            )
        return ToolHandlerOutput("Exit code: 0", metadata={"exit_code": 0})

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="run_command",
                description="run validation",
                args_schema=CommandArgs,
                handler=run_command,
                effect=ToolEffect.EXECUTE,
            ),
            ToolDefinition(
                name="git_diff",
                description="inspect diff",
                args_schema=EmptyArgs,
                handler=lambda _args, _context: "diff reviewed",
            ),
        ]
    )
    planner = StaticPlanner()
    model = ScriptedModel(
        responses=[
            _step("initial-done", "step-1"),
            _call("test-fail", "run_command", {"argv": ["pytest"]}),
            _verification("failed-report", "failed"),
            _step("correction-done", "step-2"),
            _call("test-pass", "run_command", {"argv": ["pytest"]}),
            _call("diff-pass", "git_diff", {}),
            _verification("passed-report", "passed"),
            AIMessage(content="Corrected and verified."),
        ]
    )

    result = create_agent_graph(
        model,
        task_planner=planner,
        tool_registry=registry,
    ).invoke(_input(tmp_path))

    task = result["task"]
    assert task["status"] == "completed"
    assert task["correction_attempts"] == 1
    assert task["steps"][1]["origin"] == "correction"
    assert [attempt["status"] for attempt in task["verification_attempts"]] == [
        "failed",
        "passed",
    ]
    assert planner.corrections == 1


def test_model_cannot_claim_code_verification_without_command(tmp_path: Path) -> None:
    registry = ToolRegistry(
        [
            ToolDefinition(
                name="git_diff",
                description="inspect diff",
                args_schema=EmptyArgs,
                handler=lambda _args, _context: "diff reviewed",
            )
        ]
    )
    model = ScriptedModel(
        responses=[
            _step("step-done", "step-1"),
            _call("diff", "git_diff", {}),
            _verification("false-pass", "passed"),
            AIMessage(content="Verification evidence was insufficient."),
        ]
    )

    result = create_agent_graph(
        model,
        task_planner=StaticPlanner(scope="code"),
        tool_registry=registry,
    ).invoke(_input(tmp_path))

    task = result["task"]
    assert task["status"] == "failed"
    assert task["verification_status"] == "failed"
    assert task["verification_attempts"][0]["failure_kind"] == "validation_unavailable"


def test_phase_tool_budget_fails_task_without_executing_extra_call(tmp_path: Path) -> None:
    diff_calls = 0

    def inspect_diff(_args, _context):
        nonlocal diff_calls
        diff_calls += 1
        return "diff reviewed"

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="git_diff",
                description="inspect diff",
                args_schema=EmptyArgs,
                handler=inspect_diff,
            )
        ]
    )
    model = ScriptedModel(
        responses=[
            _step("step", "step-1"),
            _call("first-diff", "git_diff", {}),
            _call("extra-diff", "git_diff", {}),
            AIMessage(content="Budget exhausted."),
        ]
    )

    result = create_agent_graph(
        model,
        task_planner=StaticPlanner(scope="docs"),
        tool_registry=registry,
        max_phase_tool_rounds=1,
    ).invoke(_input(tmp_path))

    assert result["task"]["status"] == "failed"
    assert result["task"]["phase_tool_rounds"] == 1
    assert result["task"]["total_tool_rounds"] == 1
    assert result["termination_reason"] == "failed"
    assert diff_calls == 1


def test_correction_limit_stops_after_configured_attempt(tmp_path: Path) -> None:
    def failing_command(_args, _context):
        return ToolHandlerOutput(
            "Exit code: 1",
            is_error=True,
            error_code="nonzero_exit",
            metadata={"exit_code": 1},
        )

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="run_command",
                description="run validation",
                args_schema=CommandArgs,
                handler=failing_command,
                effect=ToolEffect.EXECUTE,
            )
        ]
    )
    model = ScriptedModel(
        responses=[
            _step("initial", "step-1"),
            _call("first-failure", "run_command", {"argv": ["pytest"]}),
            _verification("first-report", "failed"),
            _step("correction", "step-2"),
            _call("second-failure", "run_command", {"argv": ["pytest"]}),
            _verification("second-report", "failed"),
            AIMessage(content="Correction limit exhausted."),
        ]
    )

    result = create_agent_graph(
        model,
        task_planner=StaticPlanner(),
        tool_registry=registry,
        max_correction_attempts=1,
    ).invoke(_input(tmp_path))

    task = result["task"]
    assert task["status"] == "failed"
    assert task["correction_attempts"] == 1
    assert len(task["verification_attempts"]) == 2
    assert all(attempt["failure_kind"] == "validation_failed" for attempt in task["verification_attempts"])


def test_verification_phase_rejects_mutating_tool_server_side(tmp_path: Path) -> None:
    writes = 0

    def write_file(_args, _context):
        nonlocal writes
        writes += 1
        return "changed"

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="apply_patch",
                description="mutate workspace",
                args_schema=EmptyArgs,
                handler=write_file,
                effect=ToolEffect.WRITE,
            )
        ]
    )
    model = ScriptedModel(
        responses=[
            _step("step", "step-1"),
            _call("forbidden", "apply_patch", {}),
            _verification("failed", "failed"),
            AIMessage(content="Verification rejected the mutation."),
        ]
    )

    result = create_agent_graph(
        model,
        task_planner=StaticPlanner(scope="docs"),
        tool_registry=registry,
    ).invoke(_input(tmp_path))

    attempt = result["task"]["verification_attempts"][0]
    assert result["task"]["status"] == "failed"
    assert attempt["failure_kind"] == "tool_not_allowed"
    assert writes == 0
