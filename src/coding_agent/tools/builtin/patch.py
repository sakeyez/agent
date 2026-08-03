"""Approval-gated unified-diff application inside the workspace."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, field_validator

from coding_agent.observability import sanitized_environment
from coding_agent.tools.contracts import (
    ToolDefinition,
    ToolEffect,
    ToolExecutionContext,
    ToolHandlerOutput,
)
from coding_agent.workspace.guard import WorkspaceGuard

MAX_PATCH_BYTES = 256 * 1024
MAX_PATCH_FILES = 50
_FORBIDDEN_MARKERS = (
    "GIT binary patch",
    "Binary files ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
    "new file mode 120000",
    "new mode 120000",
)
_HUNK_HEADER = re.compile(
    r"^@@ -\d+(?:,(?P<old_count>\d+))? \+\d+(?:,(?P<new_count>\d+))? @@"
)


class ApplyPatchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch: str = Field(
        min_length=1,
        max_length=MAX_PATCH_BYTES,
        description="Standard UTF-8 unified diff to apply",
    )

    @field_validator("patch")
    @classmethod
    def patch_must_be_supported_unified_diff(cls, value: str) -> str:
        _parse_patch(value)
        return value


@dataclass(frozen=True, slots=True)
class PatchDetails:
    paths: tuple[str, ...]
    additions: int
    deletions: int


def _header_path(line: str) -> str | None:
    raw = line[4:].split("\t", 1)[0].strip()
    if raw == "/dev/null":
        return None
    if raw.startswith(('"', "'")) or raw.endswith(('"', "'")):
        raise ValueError("quoted patch paths are not supported")
    if raw.startswith(("a/", "b/")):
        raw = raw[2:]
    if not raw:
        raise ValueError("patch contains an empty file path")
    return raw


def _parse_patch(patch: str) -> PatchDetails:
    if len(patch.encode("utf-8")) > MAX_PATCH_BYTES:
        raise ValueError("patch exceeds the 256 KiB limit")
    if any(marker in patch for marker in _FORBIDDEN_MARKERS):
        raise ValueError("binary, rename, and copy patches are not supported")

    paths: set[str] = set()
    additions = 0
    deletions = 0
    old_headers = 0
    new_headers = 0
    old_remaining = 0
    new_remaining = 0
    in_hunk = False
    for line in patch.splitlines():
        if in_hunk:
            if line.startswith("\\ No newline at end of file"):
                continue
            if line.startswith("+"):
                additions += 1
                new_remaining -= 1
            elif line.startswith("-"):
                deletions += 1
                old_remaining -= 1
            elif line.startswith(" "):
                old_remaining -= 1
                new_remaining -= 1
            if old_remaining <= 0 and new_remaining <= 0:
                in_hunk = False
            continue

        hunk = _HUNK_HEADER.match(line)
        if hunk:
            old_remaining = int(hunk.group("old_count") or "1")
            new_remaining = int(hunk.group("new_count") or "1")
            in_hunk = old_remaining > 0 or new_remaining > 0
        elif line.startswith("--- "):
            old_headers += 1
            path = _header_path(line)
            if path is not None:
                paths.add(path)
        elif line.startswith("+++ "):
            new_headers += 1
            path = _header_path(line)
            if path is not None:
                paths.add(path)

    if not old_headers or old_headers != new_headers or not paths:
        raise ValueError("patch must contain matching --- and +++ file headers")
    if len(paths) > MAX_PATCH_FILES:
        raise ValueError("patch exceeds the 50 file limit")
    return PatchDetails(tuple(sorted(paths)), additions, deletions)


def summarize_patch(arguments: BaseModel, context: ToolExecutionContext) -> str:
    args = ApplyPatchArguments.model_validate(arguments)
    try:
        details = _parse_patch(args.patch)
    except ValueError:
        return f"apply a {len(args.patch.encode('utf-8'))} byte patch"
    noun = "file" if len(details.paths) == 1 else "files"
    return (
        f"modify {len(details.paths)} {noun} "
        f"(+{details.additions}/-{details.deletions}): {', '.join(details.paths)}"
    )


def _git_apply(root: str, patch: str, *, check: bool) -> subprocess.CompletedProcess[str]:
    command = ["git", "apply", "--whitespace=nowarn", "--recount"]
    if check:
        command.append("--check")
    command.append("-")
    return subprocess.run(
        command,
        cwd=root,
        env=sanitized_environment(),
        input=patch,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def apply_patch(arguments: BaseModel, context: ToolExecutionContext) -> ToolHandlerOutput:
    args = ApplyPatchArguments.model_validate(arguments)
    try:
        details = _parse_patch(args.patch)
    except ValueError as error:
        return ToolHandlerOutput(str(error), is_error=True, error_code="invalid_patch")

    guard = WorkspaceGuard(context.workspace)
    try:
        for path in details.paths:
            guard.resolve_for_write(path)
    except ValueError as error:
        return ToolHandlerOutput(str(error), is_error=True, error_code="path_denied")

    try:
        checked = _git_apply(str(context.workspace.root), args.patch, check=True)
    except FileNotFoundError:
        return ToolHandlerOutput(
            "Git is required to apply patches but was not found",
            is_error=True,
            error_code="dependency_missing",
        )
    except subprocess.TimeoutExpired:
        return ToolHandlerOutput(
            "Patch validation exceeded the 10 second timeout",
            is_error=True,
            error_code="timeout",
        )
    if checked.returncode != 0:
        detail = (checked.stderr or checked.stdout).strip() or "patch validation failed"
        return ToolHandlerOutput(detail, is_error=True, error_code="patch_rejected")

    try:
        applied = _git_apply(str(context.workspace.root), args.patch, check=False)
    except subprocess.TimeoutExpired:
        return ToolHandlerOutput(
            "Patch application exceeded the 10 second timeout",
            is_error=True,
            error_code="timeout",
        )
    if applied.returncode != 0:
        detail = (applied.stderr or applied.stdout).strip() or "patch application failed"
        return ToolHandlerOutput(detail, is_error=True, error_code="patch_rejected")

    summary = summarize_patch(args, context)
    return ToolHandlerOutput(
        f"Patch applied successfully: {summary}",
        metadata={
            "paths": list(details.paths),
            "additions": details.additions,
            "deletions": details.deletions,
        },
    )


def patch_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="apply_patch",
            description=(
                "Apply a standard unified diff to UTF-8 text files in the active workspace. "
                "Every call requires user approval. Binary, rename, and copy patches are rejected."
            ),
            args_schema=ApplyPatchArguments,
            handler=apply_patch,
            effect=ToolEffect.WRITE,
            timeout_seconds=15,
            summary_builder=summarize_patch,
        )
    ]
