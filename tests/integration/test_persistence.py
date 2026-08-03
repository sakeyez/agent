"""Checkpoint recovery for explicit persistent task phases."""

from __future__ import annotations

from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.sqlite import SqliteSaver

from coding_agent.agents.coding.graph import create_agent_graph
from coding_agent.agents.coding.planner import CorrectionDecision, PlanningDecision


CONFIG = {"configurable": {"thread_id": "persistent-task"}}


class ScriptedModel(BaseChatModel):
    responses: list[AIMessage]
    index: int = 0

    @property
    def _llm_type(self) -> str:
        return "persistent-task-test"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, *args, **kwargs) -> ChatResult:
        response = self.responses[self.index]
        self.index += 1
        return ChatResult(generations=[ChatGeneration(message=response)])


class Planner:
    plan_calls = 0
    correction_calls = 0

    def __init__(self, scope: str = "docs") -> None:
        self.scope = scope
        self.plan_calls = 0
        self.correction_calls = 0

    def plan(self, request: str, workspace: str) -> PlanningDecision:
        self.plan_calls += 1
        return PlanningDecision(
            mode="task",
            objective=request,
            steps=["perform the persisted step"],
            change_scope=self.scope,
        )

    def plan_correction(
        self, objective: str, failure_summary: str, workspace: str
    ) -> CorrectionDecision:
        self.correction_calls += 1
        return CorrectionDecision(steps=["correct the failure"])


def _call(call_id: str, name: str, args: dict) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": name, "args": args}],
    )


def _step(call_id: str, step_id: str) -> AIMessage:
    return _call(
        call_id,
        "report_step_result",
        {"step_id": step_id, "outcome": "completed", "summary": "done"},
    )


def _verify(call_id: str, outcome: str = "passed") -> AIMessage:
    return _call(
        call_id,
        "report_verification",
        {"outcome": outcome, "summary": outcome},
    )


def _input(workspace: Path) -> dict:
    request = "update documentation persistently"
    return {
        "messages": [HumanMessage(content=request)],
        "request": request,
        "workspace": str(workspace),
        "tool_rounds": 0,
        "termination_reason": None,
    }


def _pause_after(graph, payload: dict, node: str) -> None:
    stream = graph.stream(payload, config=CONFIG, stream_mode="updates")
    for update in stream:
        if node in update:
            stream.close()
            return
    raise AssertionError(f"node {node} was not reached")


def test_sqlite_resumes_task_from_active_step(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    database = tmp_path / "task.sqlite3"
    first_planner = Planner()
    with SqliteSaver.from_conn_string(str(database)) as checkpointer:
        graph = create_agent_graph(
            ScriptedModel(responses=[]),
            checkpointer=checkpointer,
            task_planner=first_planner,
        )
        _pause_after(graph, _input(tmp_path), "prepare_step")
        snapshot = graph.get_state(CONFIG)
        assert snapshot.values["task"]["current_step_id"] == "step-1"
        assert snapshot.next == ("task_model",)

    second_planner = Planner()
    model = ScriptedModel(
        responses=[
            _step("step", "step-1"),
            _call("diff", "git_diff", {}),
            _verify("verified"),
            AIMessage(content="resumed and completed"),
        ]
    )
    with SqliteSaver.from_conn_string(str(database)) as checkpointer:
        result = create_agent_graph(
            model,
            checkpointer=checkpointer,
            task_planner=second_planner,
        ).invoke(None, config=CONFIG)

    assert result["task"]["status"] == "completed"
    assert second_planner.plan_calls == 0


def test_sqlite_resumes_from_verification_with_existing_plan(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    database = tmp_path / "verification.sqlite3"
    with SqliteSaver.from_conn_string(str(database)) as checkpointer:
        graph = create_agent_graph(
            ScriptedModel(responses=[_step("step", "step-1")]),
            checkpointer=checkpointer,
            task_planner=Planner(),
        )
        _pause_after(graph, _input(tmp_path), "start_verification")
        snapshot = graph.get_state(CONFIG)
        assert snapshot.values["task"]["verification_status"] == "running"
        assert snapshot.next == ("verification_model",)

    model = ScriptedModel(
        responses=[
            _call("diff", "git_diff", {}),
            _verify("verified"),
            AIMessage(content="verification resumed"),
        ]
    )
    with SqliteSaver.from_conn_string(str(database)) as checkpointer:
        result = create_agent_graph(
            model,
            checkpointer=checkpointer,
            task_planner=Planner(),
        ).invoke(None, config=CONFIG)

    assert result["task"]["verification_attempts"][0]["status"] == "passed"
    assert result["task"]["total_tool_rounds"] == 1


def test_sqlite_resumes_before_correction_planning(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    database = tmp_path / "correction.sqlite3"
    first_model = ScriptedModel(
        responses=[
            _step("step", "step-1"),
            _call(
                "test-fail",
                "run_command",
                {"argv": ["python", "-m", "pytest", "missing-test-file.py"]},
            ),
            _verify("failed", "failed"),
        ]
    )
    with SqliteSaver.from_conn_string(str(database)) as checkpointer:
        graph = create_agent_graph(
            first_model,
            checkpointer=checkpointer,
            task_planner=Planner(scope="code"),
        )
        stream = graph.stream(_input(tmp_path), config=CONFIG, stream_mode="updates")
        for update in stream:
            task = update.get("verification_tools", {}).get("task")
            if task and task["status"] == "correcting":
                stream.close()
                break
        else:
            raise AssertionError("correction state was not reached")
        snapshot = graph.get_state(CONFIG)
        assert snapshot.next == ("prepare_correction",)
        assert snapshot.values["task"]["verification_attempts"][0]["status"] == "failed"

    resumed_planner = Planner(scope="code")
    second_model = ScriptedModel(
        responses=[
            _step("correction", "step-2"),
            _call(
                "test-pass",
                "run_command",
                {"argv": ["pytest", "--version"]},
            ),
            _call("diff", "git_diff", {}),
            _verify("passed"),
            AIMessage(content="correction resumed and passed"),
        ]
    )
    with SqliteSaver.from_conn_string(str(database)) as checkpointer:
        result = create_agent_graph(
            second_model,
            checkpointer=checkpointer,
            task_planner=resumed_planner,
        ).invoke(None, config=CONFIG)

    assert result["task"]["status"] == "completed"
    assert result["task"]["correction_attempts"] == 1
    assert resumed_planner.plan_calls == 0
    assert resumed_planner.correction_calls == 1
