from __future__ import annotations

from collections.abc import Callable, Iterator
from io import StringIO
import subprocess

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from coding_agent.cli import run_cli
from coding_agent.agents.coding.planner import CorrectionDecision, PlanningDecision


class CliToolModel(BaseChatModel):
    responses: list[AIMessage]
    response_index: int = 0

    @property
    def _llm_type(self) -> str:
        return "cli-tool-model"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, *args, **kwargs) -> ChatResult:
        response = self.responses[self.response_index]
        self.response_index += 1
        return ChatResult(generations=[ChatGeneration(message=response)])


class DocumentationTaskPlanner:
    def plan(self, request: str, workspace: str) -> PlanningDecision:
        return PlanningDecision(
            mode="task",
            objective=request,
            steps=["apply the requested text change"],
            change_scope="docs",
        )

    def plan_correction(
        self, objective: str, failure_summary: str, workspace: str
    ) -> CorrectionDecision:
        return CorrectionDecision(steps=["correct the failed check"])


def _reader(values: list[str]) -> Callable[[str], str]:
    iterator: Iterator[str] = iter(values)
    return lambda _prompt: next(iterator)


def test_cli_runs_tool_loop_and_only_renders_model_text(settings) -> None:
    (settings.workspace / "sample.txt").write_text("workspace content\n", encoding="utf-8")
    model = CliToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "read-1",
                        "name": "read_file",
                        "args": {"path": "sample.txt"},
                    }
                ],
            ),
            AIMessage(content="I found workspace content."),
        ]
    )
    stdout = StringIO()
    stderr = StringIO()

    result = run_cli(
        settings,
        model=model,
        input_fn=_reader(["inspect", "/exit"]),
        stdout=stdout,
        stderr=stderr,
    )

    assert result == 0
    assert stdout.getvalue() == "Kimi> I found workspace content.\n"
    assert stderr.getvalue() == ""
    assert model.response_index == 2


def test_cli_approves_and_applies_patch(settings) -> None:
    subprocess.run(["git", "init", "-q"], cwd=settings.workspace, check=True)
    (settings.workspace / "sample.txt").write_text("old\n", encoding="utf-8")
    patch = "--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1 @@\n-old\n+new\n"
    model = CliToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "patch-1",
                        "name": "apply_patch",
                        "args": {"patch": patch},
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "step-done",
                        "name": "report_step_result",
                        "args": {
                            "step_id": "step-1",
                            "outcome": "completed",
                            "summary": "patch applied",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[{"id": "diff-1", "name": "git_diff", "args": {}}],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "verify-1",
                        "name": "report_verification",
                        "args": {"outcome": "passed", "summary": "diff reviewed"},
                    }
                ],
            ),
            AIMessage(content="Patch complete and verified."),
        ]
    )
    stdout = StringIO()

    result = run_cli(
        settings,
        model=model,
        input_fn=_reader(["change it", "yes", "/exit"]),
        stdout=stdout,
        stderr=StringIO(),
        task_planner=DocumentationTaskPlanner(),
    )

    assert result == 0
    assert (settings.workspace / "sample.txt").read_text(encoding="utf-8") == "new\n"
    assert "需要批准 [apply_patch]" in stdout.getvalue()
    assert "已批准" in stdout.getvalue()
    assert "Patch complete and verified." in stdout.getvalue()
    assert "[验证] passed" in stdout.getvalue()
    assert settings.audit_path and settings.audit_path.exists()


def test_cli_denial_returns_tool_error_and_continues(settings) -> None:
    subprocess.run(["git", "init", "-q"], cwd=settings.workspace, check=True)
    (settings.workspace / "sample.txt").write_text("old\n", encoding="utf-8")
    patch = "--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1 @@\n-old\n+new\n"
    model = CliToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "patch-1",
                        "name": "apply_patch",
                        "args": {"patch": patch},
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "step-failed",
                        "name": "report_step_result",
                        "args": {
                            "step_id": "step-1",
                            "outcome": "failed",
                            "summary": "The change was denied.",
                        },
                    }
                ],
            ),
            AIMessage(content="The change was denied."),
        ]
    )
    stdout = StringIO()

    result = run_cli(
        settings,
        model=model,
        input_fn=_reader(["change it", "no", "/exit"]),
        stdout=stdout,
        stderr=StringIO(),
        task_planner=DocumentationTaskPlanner(),
    )

    assert result == 0
    assert (settings.workspace / "sample.txt").read_text(encoding="utf-8") == "old\n"
    assert "已拒绝" in stdout.getvalue()
    assert "The change was denied." in stdout.getvalue()
