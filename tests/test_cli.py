from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from io import StringIO

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.outputs import ChatResult

from coding_agent import cli
from coding_agent.config import ConfigError, Settings


class FailingChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "failing-test-model"

    def _generate(self, *args, **kwargs) -> ChatResult:
        raise RuntimeError("request broke")


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
