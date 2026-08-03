"""Workspace-scoped read-only filesystem tools."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from coding_agent.tools.contracts import (
    ToolDefinition,
    ToolExecutionContext,
)
from coding_agent.workspace.guard import WorkspaceAccessError, WorkspaceGuard

MAX_LIST_RESULTS = 200
MAX_FILE_BYTES = 1024 * 1024


class ListFilesArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(default=".", description="Workspace-relative directory to list")
    pattern: str = Field(default="*", description="Glob pattern matched against relative paths")
    recursive: bool = Field(default=True, description="Whether to include nested entries")


class ReadFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Workspace-relative file path")
    start_line: int = Field(default=1, ge=1, description="First 1-based line to return")
    line_count: int = Field(default=200, ge=1, le=500, description="Maximum lines to return")


def _guard(context: ToolExecutionContext) -> WorkspaceGuard:
    return WorkspaceGuard(context.workspace)


def _visible_entries(directory: Path, *, recursive: bool) -> list[Path]:
    if not recursive:
        return list(directory.iterdir())

    entries: list[Path] = []
    for current, directories, files in os.walk(directory, followlinks=False):
        current_path = Path(current)
        directories[:] = [
            name
            for name in directories
            if name not in {".git", ".venv", "node_modules", "__pycache__", ".coding_agent"}
        ]
        entries.extend(current_path / name for name in directories)
        entries.extend(current_path / name for name in files)
    return entries


def list_files(arguments: BaseModel, context: ToolExecutionContext) -> str:
    args = ListFilesArguments.model_validate(arguments)
    guard = _guard(context)
    directory = guard.resolve(args.path)
    if not directory.exists():
        raise WorkspaceAccessError("directory does not exist")
    if not directory.is_dir():
        raise WorkspaceAccessError("path is not a directory")

    results: list[str] = []
    for entry in _visible_entries(directory, recursive=args.recursive):
        if entry.is_symlink():
            try:
                guard.resolve(entry)
            except WorkspaceAccessError:
                continue
        if entry.is_file() and guard.is_sensitive(entry):
            continue
        relative = guard.relative(entry)
        if not fnmatch.fnmatch(relative, args.pattern) and not fnmatch.fnmatch(
            entry.name, args.pattern
        ):
            continue
        results.append(relative + ("/" if entry.is_dir() else ""))
        if len(results) >= MAX_LIST_RESULTS:
            break

    results.sort()
    if not results:
        return "No files found."
    suffix = "\n[results limited to 200 entries]" if len(results) == MAX_LIST_RESULTS else ""
    return "\n".join(results) + suffix


def read_file(arguments: BaseModel, context: ToolExecutionContext) -> str:
    args = ReadFileArguments.model_validate(arguments)
    guard = _guard(context)
    path = guard.resolve(args.path)
    if not path.exists():
        raise WorkspaceAccessError("file does not exist")
    if not path.is_file():
        raise WorkspaceAccessError("path is not a file")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise WorkspaceAccessError("file exceeds the 1 MiB read limit")

    data = path.read_bytes()
    if b"\x00" in data:
        raise WorkspaceAccessError("binary files cannot be read")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkspaceAccessError("file is not valid UTF-8 text") from error

    lines = text.splitlines()
    start_index = args.start_line - 1
    selected = lines[start_index : start_index + args.line_count]
    if not selected:
        return f"No content at or after line {args.start_line}."
    return "\n".join(
        f"{line_number}: {line}"
        for line_number, line in enumerate(selected, start=args.start_line)
    )


def filesystem_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="list_files",
            description=(
                "List files and directories inside the active workspace. "
                "Sensitive files and generated dependency directories are omitted."
            ),
            args_schema=ListFilesArguments,
            handler=list_files,
        ),
        ToolDefinition(
            name="read_file",
            description=(
                "Read a UTF-8 text file inside the active workspace with numbered lines. "
                "Sensitive files cannot be read."
            ),
            args_schema=ReadFileArguments,
            handler=read_file,
        ),
    ]
