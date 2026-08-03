"""Workspace-scoped literal text search."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from coding_agent.tools.builtin.filesystem import MAX_FILE_BYTES
from coding_agent.tools.contracts import ToolDefinition, ToolExecutionContext
from coding_agent.workspace.guard import WorkspaceAccessError, WorkspaceGuard

MAX_SEARCH_RESULTS = 100


class SearchTextArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(description="Literal text to find")
    path: str = Field(default=".", description="Workspace-relative directory or file")
    pattern: str = Field(default="*", description="Glob pattern for files to search")
    case_sensitive: bool = Field(default=False, description="Use case-sensitive matching")

    @field_validator("query")
    @classmethod
    def query_must_not_be_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("query must not be empty")
        return value


def _candidate_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]

    files: list[Path] = []
    for current, directories, names in os.walk(root, followlinks=False):
        directories[:] = [
            name
            for name in directories
            if name not in {".git", ".venv", "node_modules", "__pycache__", ".coding_agent"}
        ]
        current_path = Path(current)
        files.extend(current_path / name for name in names)
    return files


def search_text(arguments: BaseModel, context: ToolExecutionContext) -> str:
    args = SearchTextArguments.model_validate(arguments)
    guard = WorkspaceGuard(context.workspace)
    root = guard.resolve(args.path)
    if not root.exists():
        raise WorkspaceAccessError("search path does not exist")

    needle = args.query if args.case_sensitive else args.query.casefold()
    results: list[str] = []
    for path in _candidate_files(root):
        try:
            resolved = guard.resolve(path)
            relative = guard.relative(resolved)
            if guard.is_sensitive(resolved):
                continue
            if not fnmatch.fnmatch(relative, args.pattern) and not fnmatch.fnmatch(
                resolved.name, args.pattern
            ):
                continue
            if resolved.stat().st_size > MAX_FILE_BYTES:
                continue
            data = resolved.read_bytes()
            if b"\x00" in data:
                continue
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError, WorkspaceAccessError):
            continue

        for line_number, line in enumerate(text.splitlines(), start=1):
            haystack = line if args.case_sensitive else line.casefold()
            if needle in haystack:
                results.append(f"{relative}:{line_number}: {line}")
                if len(results) >= MAX_SEARCH_RESULTS:
                    return "\n".join(results) + "\n[results limited to 100 matches]"

    return "\n".join(results) if results else "No matches found."


def search_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="search_text",
            description=(
                "Search for literal text in UTF-8 files inside the active workspace. "
                "Returns relative paths, line numbers, and matching lines."
            ),
            args_schema=SearchTextArguments,
            handler=search_text,
        )
    ]
