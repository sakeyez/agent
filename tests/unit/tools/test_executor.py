from __future__ import annotations

import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from coding_agent.tools import (
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutor,
    ToolRegistry,
)
from coding_agent.workspace import WorkspaceContext


class EchoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


def _context(path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(workspace=WorkspaceContext.from_path(path))


def _registry(handler=lambda arguments, _context: arguments.text) -> ToolRegistry:
    return ToolRegistry(
        [
            ToolDefinition(
                name="echo",
                description="Echo text",
                args_schema=EchoArguments,
                handler=handler,
            )
        ]
    )


def test_executor_validates_and_runs_tool(tmp_path: Path) -> None:
    result = ToolExecutor(_registry()).execute(
        ToolCall(call_id="call-1", name="echo", arguments={"text": "hello"}),
        _context(tmp_path),
    )

    assert result.content == "hello"
    assert result.is_error is False


def test_executor_returns_errors_for_unknown_tool_and_invalid_arguments(tmp_path: Path) -> None:
    executor = ToolExecutor(_registry())

    unknown = executor.execute(
        ToolCall(call_id="call-1", name="missing", arguments={}), _context(tmp_path)
    )
    invalid = executor.execute(
        ToolCall(call_id="call-2", name="echo", arguments={}), _context(tmp_path)
    )

    assert unknown.error_code == "unknown_tool"
    assert invalid.error_code == "invalid_arguments"
    assert unknown.is_error and invalid.is_error


def test_executor_normalizes_exceptions_and_truncates_utf8_output(tmp_path: Path) -> None:
    def fail(_arguments, _context):
        raise OSError(f"cannot read {tmp_path / 'private.txt'}")

    failed = ToolExecutor(_registry(fail)).execute(
        ToolCall(call_id="call-1", name="echo", arguments={"text": "x"}),
        _context(tmp_path),
    )
    truncated = ToolExecutor(_registry(), max_output_bytes=32).execute(
        ToolCall(call_id="call-2", name="echo", arguments={"text": "界" * 30}),
        _context(tmp_path),
    )

    assert failed.error_code == "execution_error"
    assert "cannot read" in failed.content
    assert str(tmp_path) not in failed.content
    assert "<workspace>" in failed.content
    assert truncated.truncated is True
    assert "[output truncated]" in truncated.content
    assert len(truncated.content.encode("utf-8")) <= 32


def test_executor_times_out_tool(tmp_path: Path) -> None:
    def slow(arguments, _context):
        time.sleep(0.05)
        return arguments.text

    result = ToolExecutor(_registry(slow), timeout_seconds=0.001).execute(
        ToolCall(call_id="call-1", name="echo", arguments={"text": "late"}),
        _context(tmp_path),
    )

    assert result.error_code == "timeout"
