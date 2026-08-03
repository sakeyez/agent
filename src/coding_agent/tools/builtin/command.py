"""Policy-controlled single-process command execution."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from coding_agent.observability import sanitized_environment
from coding_agent.tools.contracts import (
    ToolDefinition,
    ToolEffect,
    ToolExecutionContext,
    ToolHandlerOutput,
)
from coding_agent.workspace.guard import WorkspaceAccessError, WorkspaceGuard

MAX_COMMAND_OUTPUT_BYTES = 128 * 1024


class RunCommandArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    argv: list[str] = Field(min_length=1, max_length=64, description="Program and arguments")
    cwd: str = Field(default=".", description="Workspace-relative working directory")
    timeout_seconds: int = Field(default=60, ge=1, le=120)

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, value: list[str]) -> list[str]:
        if any(not item or "\x00" in item for item in value):
            raise ValueError("argv entries must be non-empty and cannot contain NUL bytes")
        return value


def summarize_command(arguments: BaseModel, context: ToolExecutionContext) -> str:
    args = RunCommandArguments.model_validate(arguments)
    return f"run in {args.cwd}: {shlex.join(args.argv)}"


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.kill()
    except ProcessLookupError:
        pass


def _read_bounded(stream, limit: int = MAX_COMMAND_OUTPUT_BYTES) -> str:
    stream.flush()
    size = stream.tell()
    stream.seek(0)
    if size <= limit:
        data = stream.read()
    else:
        half = limit // 2
        head = stream.read(half)
        stream.seek(-half, os.SEEK_END)
        tail = stream.read(half)
        data = head + b"\n[command output middle omitted]\n" + tail
    return data.decode("utf-8", errors="replace")


def run_command(arguments: BaseModel, context: ToolExecutionContext) -> ToolHandlerOutput:
    args = RunCommandArguments.model_validate(arguments)
    guard = WorkspaceGuard(context.workspace)
    try:
        cwd = guard.resolve(args.cwd)
        if not cwd.exists() or not cwd.is_dir():
            raise WorkspaceAccessError("command cwd must be an existing directory")
    except WorkspaceAccessError as error:
        return ToolHandlerOutput(str(error), is_error=True, error_code="path_denied")

    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    start_new_session = os.name != "nt"
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            process = subprocess.Popen(
                args.argv,
                cwd=Path(cwd),
                env=sanitized_environment(),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                shell=False,
                creationflags=creationflags,
                start_new_session=start_new_session,
            )
        except FileNotFoundError:
            return ToolHandlerOutput(
                f"Executable not found: {args.argv[0]}",
                is_error=True,
                error_code="dependency_missing",
            )
        except OSError as error:
            return ToolHandlerOutput(str(error), is_error=True, error_code="execution_error")

        timed_out = False
        try:
            exit_code = process.wait(timeout=args.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process_tree(process)
            exit_code = process.wait(timeout=5)

        stdout = _read_bounded(stdout_file)
        stderr = _read_bounded(stderr_file)

    sections = [f"Exit code: {exit_code}"]
    if stdout:
        sections.append(f"STDOUT:\n{stdout.rstrip()}")
    if stderr:
        sections.append(f"STDERR:\n{stderr.rstrip()}")
    content = "\n\n".join(sections)
    metadata = {
        "exit_code": exit_code,
        "cwd": guard.relative(cwd),
        "timed_out": timed_out,
    }
    if timed_out:
        return ToolHandlerOutput(
            content,
            is_error=True,
            error_code="timeout",
            metadata=metadata,
        )
    if exit_code != 0:
        return ToolHandlerOutput(
            content,
            is_error=True,
            error_code="nonzero_exit",
            metadata=metadata,
        )
    return ToolHandlerOutput(content, metadata=metadata)


def command_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="run_command",
            description=(
                "Run one program with an argument array inside the active workspace. "
                "Shell syntax, custom environment variables, and destructive commands are not allowed."
            ),
            args_schema=RunCommandArguments,
            handler=run_command,
            effect=ToolEffect.EXECUTE,
            timeout_seconds=125,
            summary_builder=summarize_command,
        )
    ]
