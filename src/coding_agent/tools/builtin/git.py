"""Read-only Git status and diff inspection."""

from __future__ import annotations

import subprocess
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from coding_agent.observability import sanitized_environment
from coding_agent.tools.contracts import (
    ToolDefinition,
    ToolExecutionContext,
    ToolHandlerOutput,
)
from coding_agent.workspace.guard import WorkspaceGuard


class GitDiffArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["all", "unstaged", "staged"] = "all"
    paths: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("paths")
    @classmethod
    def paths_must_not_be_empty(cls, value: list[str]) -> list[str]:
        if any(not item for item in value):
            raise ValueError("paths cannot contain empty values")
        return value


def summarize_git_diff(arguments: BaseModel, context: ToolExecutionContext) -> str:
    args = GitDiffArguments.model_validate(arguments)
    suffix = f" for {', '.join(args.paths)}" if args.paths else ""
    return f"inspect {args.mode} Git changes{suffix}"


def _run_git(root: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        env=sanitized_environment(),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def git_diff(arguments: BaseModel, context: ToolExecutionContext) -> ToolHandlerOutput:
    args = GitDiffArguments.model_validate(arguments)
    guard = WorkspaceGuard(context.workspace)
    paths: list[str] = []
    try:
        for requested in args.paths:
            paths.append(guard.relative(guard.resolve(requested)))
    except ValueError as error:
        return ToolHandlerOutput(str(error), is_error=True, error_code="path_denied")
    pathspec = ["--", *paths] if paths else []

    try:
        status = _run_git(
            str(context.workspace.root),
            ["status", "--short", "--untracked-files=all", *pathspec],
        )
    except FileNotFoundError:
        return ToolHandlerOutput(
            "Git is required to inspect changes but was not found",
            is_error=True,
            error_code="dependency_missing",
        )
    except subprocess.TimeoutExpired:
        return ToolHandlerOutput(
            "Git inspection exceeded the 10 second timeout",
            is_error=True,
            error_code="timeout",
        )
    if status.returncode != 0:
        detail = (status.stderr or status.stdout).strip() or "Git status failed"
        code = "not_git_repository" if "not a git repository" in detail.lower() else "git_error"
        return ToolHandlerOutput(detail, is_error=True, error_code=code)

    sections = ["STATUS:\n" + (status.stdout.rstrip() or "No changes.")]
    base_diff = ["diff", "--no-ext-diff", "--no-textconv", "--unified=3"]
    modes = [args.mode] if args.mode != "all" else ["unstaged", "staged"]
    for mode in modes:
        diff_args = [*base_diff]
        if mode == "staged":
            diff_args.append("--cached")
        try:
            diff = _run_git(str(context.workspace.root), [*diff_args, *pathspec])
        except subprocess.TimeoutExpired:
            return ToolHandlerOutput(
                "Git inspection exceeded the 10 second timeout",
                is_error=True,
                error_code="timeout",
            )
        if diff.returncode != 0:
            detail = (diff.stderr or diff.stdout).strip() or "Git diff failed"
            return ToolHandlerOutput(detail, is_error=True, error_code="git_error")
        sections.append(f"{mode.upper()} DIFF:\n" + (diff.stdout.rstrip() or "No diff."))

    return ToolHandlerOutput(
        "\n\n".join(sections),
        metadata={"mode": args.mode, "paths": paths},
    )


def git_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="git_diff",
            description=(
                "Show Git status plus staged, unstaged, or all diffs in the active workspace. "
                "Untracked files appear in status and can be inspected with read_file."
            ),
            args_schema=GitDiffArguments,
            handler=git_diff,
            summary_builder=summarize_git_diff,
        )
    ]
