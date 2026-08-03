from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from io import StringIO

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatResult
from langchain_core.outputs import ChatGeneration
from langgraph.checkpoint.sqlite import SqliteSaver

from coding_agent import cli
from coding_agent.agents.coding.graph import create_agent_graph
from coding_agent.agents.coding.planner import CorrectionDecision, PlanningDecision
from coding_agent.config import ConfigError, Settings
from coding_agent.persistence import open_sqlite_persistence


class FailingChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "failing-test-model"

    def _generate(self, *args, **kwargs) -> ChatResult:
        raise RuntimeError("request broke")


class TaskCliModel(BaseChatModel):
    responses: list[AIMessage]
    index: int = 0

    @property
    def _llm_type(self) -> str:
        return "task-cli-test"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, *args, **kwargs) -> ChatResult:
        response = self.responses[self.index]
        self.index += 1
        return ChatResult(generations=[ChatGeneration(message=response)])


class InterruptingTaskModel(TaskCliModel):
    def _generate(self, *args, **kwargs) -> ChatResult:
        raise KeyboardInterrupt


class DocsPlanner:
    plan_calls: int = 0

    def plan(self, request: str, workspace: str) -> PlanningDecision:
        self.plan_calls += 1
        return PlanningDecision(
            mode="task",
            objective=request,
            steps=["update documentation"],
            change_scope="docs",
        )

    def plan_correction(
        self, objective: str, failure_summary: str, workspace: str
    ) -> CorrectionDecision:
        return CorrectionDecision(steps=["correct documentation"])


def _call(call_id: str, name: str, args: dict) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": name, "args": args}],
    )


def _completed_task_responses() -> list[AIMessage]:
    return [
        _call(
            "step",
            "report_step_result",
            {"step_id": "step-1", "outcome": "completed", "summary": "done"},
        ),
        _call("diff", "git_diff", {}),
        _call(
            "verified",
            "report_verification",
            {"outcome": "passed", "summary": "diff reviewed"},
        ),
        AIMessage(content="Documentation task completed."),
    ]


def _reader(values: list[str]) -> Callable[[str], str]:
    iterator: Iterator[str] = iter(values)
    return lambda _prompt: next(iterator)


def test_cli_streams_response_ignores_empty_input_and_exits(settings: Settings) -> None:
    stdout = StringIO()
    stderr = StringIO()

    result = cli.run_cli(
        settings,
        model=FakeListChatModel(responses=["streamed reply"]),
        input_fn=_reader(["", "hello", "/exit"]),
        stdout=stdout,
        stderr=stderr,
    )

    assert result == 0
    assert stdout.getvalue() == "Kimi> streamed reply\n"
    assert stderr.getvalue() == ""


@pytest.mark.parametrize("terminal_error", [EOFError, KeyboardInterrupt])
def test_cli_handles_terminal_exit_without_traceback(
    settings: Settings, terminal_error: type[BaseException]
) -> None:
    def stop(_prompt: str) -> str:
        raise terminal_error

    stdout = StringIO()
    stderr = StringIO()
    result = cli.run_cli(
        settings,
        model=FakeListChatModel(responses=["unused"]),
        input_fn=stop,
        stdout=stdout,
        stderr=stderr,
    )

    assert result == 0
    assert "Traceback" not in stdout.getvalue() + stderr.getvalue()


def test_cli_reports_request_error_and_keeps_running(settings: Settings) -> None:
    stdout = StringIO()
    stderr = StringIO()
    model = FailingChatModel()

    result = cli.run_cli(
        settings,
        model=model,
        input_fn=_reader(["hello", "/exit"]),
        stdout=stdout,
        stderr=stderr,
    )

    assert result == 0
    assert "请求失败" in stderr.getvalue()
    assert "request broke" in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


def test_main_prints_short_configuration_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: (_ for _ in ()).throw(ConfigError("missing")))

    assert cli.main() == 2
    captured = capsys.readouterr()
    assert "配置错误" in captured.err
    assert "Traceback" not in captured.err


def test_cli_isolates_and_redacts_plugin_startup_error(settings: Settings) -> None:
    plugin = settings.workspace / "plugins" / "leaky-plugin"
    plugin.mkdir(parents=True)
    (plugin / "plugin.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                'name = "leaky-plugin"',
                'version = "1.0.0"',
                'description = "Broken test plugin"',
                'entrypoint = "plugin:register"',
            ]
        ),
        encoding="utf-8",
    )
    (plugin / "plugin.py").write_text(
        'raise RuntimeError("test-secret-key")\n', encoding="utf-8"
    )
    settings.plugins_enabled = True
    stdout = StringIO()
    stderr = StringIO()

    result = cli.run_cli(
        settings,
        model=FakeListChatModel(responses=["unused"]),
        input_fn=_reader(["/exit"]),
        stdout=stdout,
        stderr=stderr,
    )

    assert result == 0
    assert "插件 leaky-plugin 加载失败" in stderr.getvalue()
    assert "test-secret-key" not in stderr.getvalue()
    assert "***" in stderr.getvalue()


def test_module_entrypoint_can_start_and_exit(tmp_path) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "KIMI_API_KEY": "test-key",
            "KIMI_MODEL": "test-model",
            "AGENT_WORKSPACE": str(tmp_path),
            "AGENT_DB_PATH": str(tmp_path / "entrypoint.sqlite3"),
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", "coding_agent.cli"],
        input="/exit\n",
        text=True,
        capture_output=True,
        env=environment,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    assert "Traceback" not in completed.stderr


def test_cli_status_shows_persisted_plan_and_verification(settings: Settings) -> None:
    subprocess.run(["git", "init", "-q"], cwd=settings.workspace, check=True)
    stdout = StringIO()

    result = cli.run_cli(
        settings,
        model=TaskCliModel(responses=_completed_task_responses()),
        task_planner=DocsPlanner(),
        input_fn=_reader(["update docs", "/status", "/exit"]),
        stdout=stdout,
        stderr=StringIO(),
    )

    assert result == 0
    rendered = stdout.getvalue()
    assert "[任务] planning" in rendered
    assert "[验证] passed" in rendered
    assert "状态: completed | 验证: passed" in rendered
    assert "[completed] step-1 update documentation" in rendered


def test_cli_can_cancel_task_after_interrupt(settings: Settings) -> None:
    stdout = StringIO()

    result = cli.run_cli(
        settings,
        model=InterruptingTaskModel(responses=[]),
        task_planner=DocsPlanner(),
        input_fn=_reader(["update docs", "/cancel", "/status", "/exit"]),
        stdout=stdout,
        stderr=StringIO(),
    )

    assert result == 0
    rendered = stdout.getvalue()
    assert "最近的检查点暂停" in rendered
    assert "任务已取消" in rendered
    assert "状态: cancelled" in rendered
    assert settings.database_path is not None
    with open_sqlite_persistence(settings.database_path) as persistence:
        snapshot = create_agent_graph(
            TaskCliModel(responses=[]),
            checkpointer=persistence.checkpointer,
        ).get_state(cli.THREAD_CONFIG)
    assert snapshot.next == ()


def test_cli_automatically_resumes_unfinished_task(settings: Settings) -> None:
    subprocess.run(["git", "init", "-q"], cwd=settings.workspace, check=True)
    planner = DocsPlanner()
    request = "update docs"
    assert settings.database_path is not None
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(settings.database_path)) as checkpointer:
        graph = create_agent_graph(
            TaskCliModel(responses=[]),
            checkpointer=checkpointer,
            task_planner=planner,
        )
        stream = graph.stream(
            {
                "messages": [HumanMessage(content=request)],
                "request": request,
                "workspace": str(settings.workspace),
                "tool_rounds": 0,
            },
            config=cli.THREAD_CONFIG,
            stream_mode="updates",
        )
        for update in stream:
            if "prepare_step" in update:
                stream.close()
                break

    resumed_planner = DocsPlanner()
    stdout = StringIO()
    result = cli.run_cli(
        settings,
        model=TaskCliModel(responses=_completed_task_responses()),
        task_planner=resumed_planner,
        input_fn=_reader(["/exit"]),
        stdout=stdout,
        stderr=StringIO(),
    )

    assert result == 0
    assert "正在恢复未完成任务" in stdout.getvalue()
    assert "Documentation task completed." in stdout.getvalue()
    assert resumed_planner.plan_calls == 0


def test_cli_manages_isolated_sessions(settings: Settings) -> None:
    stdout = StringIO()
    result = cli.run_cli(
        settings,
        model=FakeListChatModel(responses=["one", "two", "three"]),
        input_fn=_reader(
            [
                "hello default",
                "/new work",
                "hello work",
                "/use default",
                "again default",
                "/sessions",
                "/exit",
            ]
        ),
        stdout=stdout,
        stderr=StringIO(),
    )

    assert result == 0
    assert "work" in stdout.getvalue()
    assert settings.database_path is not None
    with open_sqlite_persistence(settings.database_path) as persistence:
        stored_sessions = persistence.sessions.list()
        work = next(item for item in stored_sessions if item.name == "work")
        graph = create_agent_graph(
            FakeListChatModel(responses=["unused"]),
            checkpointer=persistence.checkpointer,
        )
        default_state = graph.get_state(cli.THREAD_CONFIG)
        work_state = graph.get_state(
            {"configurable": {"thread_id": str(work.id)}}
        )

    assert [item.content for item in default_state.values["messages"]] == [
        "hello default",
        "one",
        "again default",
        "three",
    ]
    assert [item.content for item in work_state.values["messages"]] == [
        "hello work",
        "two",
    ]


def test_cli_persists_model_selection_and_recovers_from_removed_model(tmp_path) -> None:
    database = tmp_path / "models.sqlite3"
    first_settings = Settings(
        KIMI_API_KEY="kimi-secret",
        AGENT_MODELS="kimi:kimi-a,openai-compatible:coder",
        AGENT_DEFAULT_MODEL="kimi:kimi-a",
        OPENAI_COMPAT_API_KEY="compat-secret",
        OPENAI_COMPAT_BASE_URL="http://localhost:8000/v1",
        AGENT_WORKSPACE=tmp_path,
        AGENT_DB_PATH=database,
        _env_file=None,
    )
    first_output = StringIO()
    assert cli.run_cli(
        first_settings,
        model=FakeListChatModel(responses=["unused"]),
        input_fn=_reader(["/models", "/model openai-compatible:coder", "/exit"]),
        stdout=first_output,
        stderr=StringIO(),
    ) == 0
    assert "openai-compatible:coder" in first_output.getvalue()

    second_settings = Settings(
        KIMI_API_KEY="kimi-secret",
        KIMI_MODEL="kimi-a",
        AGENT_WORKSPACE=tmp_path,
        AGENT_DB_PATH=database,
        _env_file=None,
    )
    second_output = StringIO()
    second_errors = StringIO()
    assert cli.run_cli(
        second_settings,
        model=FakeListChatModel(responses=["unused"]),
        input_fn=_reader(["/model kimi:kimi-a", "/session", "/exit"]),
        stdout=second_output,
        stderr=second_errors,
    ) == 0

    assert "当前会话模型不可用：openai-compatible:coder" in second_errors.getvalue()
    assert "模型: kimi:kimi-a" in second_output.getvalue()
