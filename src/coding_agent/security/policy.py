"""Risk classification and execution policy decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from coding_agent.tools.contracts import (
    ToolDefinition,
    ToolEffect,
    ToolExecutionContext,
)


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PolicyResult:
    decision: PolicyDecision
    reason: str
    summary: str
    operation_kind: str | None = None


class OperationPolicy(Protocol):
    def evaluate(
        self,
        tool: ToolDefinition,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> PolicyResult: ...


_SHELL_EXECUTABLES = {
    "bash",
    "cmd",
    "cmd.exe",
    "dash",
    "fish",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
    "sh",
    "zsh",
}
_DESTRUCTIVE_EXECUTABLES = {
    "del",
    "erase",
    "format",
    "kill",
    "reboot",
    "rm",
    "rmdir",
    "shutdown",
    "taskkill",
}
_PYTHON_NAME = re.compile(r"^(python|python\d+(?:\.\d+)?|py)(?:\.exe)?$")


def _basename(value: str) -> str:
    return Path(value.replace("\\", "/")).name.lower()


def _executable_name(value: str) -> str:
    name = _basename(value)
    return name[:-4] if name.endswith(".exe") else name


def _validation_command_kind(argv: list[str]) -> str | None:
    if not argv:
        return None
    if _basename(argv[0]) != argv[0].lower():
        return None
    executable = _executable_name(argv[0])
    args = argv[1:]
    if executable in {"pytest", "mypy", "pyright"}:
        return "validation"
    if executable == "ruff":
        return "validation" if (
            bool(args)
            and args[0] == "check"
            and not any(item.startswith("--fix") for item in args)
        ) else None
    if _PYTHON_NAME.match(executable):
        return (
            _validation_command_kind(args[1:])
            if len(args) >= 2 and args[0] == "-m"
            else None
        )
    if executable == "uv":
        return (
            _validation_command_kind(args[1:])
            if len(args) >= 2 and args[0] == "run"
            else None
        )
    if executable == "git" and args:
        return "inspection" if args[0] in {"diff", "log", "rev-parse", "show", "status"} else None
    return None


def is_safe_validation_command(argv: list[str]) -> bool:
    return _validation_command_kind(argv) is not None


def _is_destructive_git(argv: list[str]) -> bool:
    if len(argv) < 2 or _executable_name(argv[0]) != "git":
        return False
    subcommand = argv[1].lower()
    remaining = [item.lower() for item in argv[2:]]
    return (
        subcommand in {"clean", "restore"}
        or (subcommand == "reset" and "--hard" in remaining)
        or (subcommand == "checkout" and "--" in remaining)
    )


class DefaultOperationPolicy:
    """Conservative policy for the built-in coding tools."""

    def evaluate(
        self,
        tool: ToolDefinition,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> PolicyResult:
        summary = (
            tool.summary_builder(arguments, context)
            if tool.summary_builder is not None
            else tool.name
        )
        if tool.effect is ToolEffect.READ:
            return PolicyResult(PolicyDecision.ALLOW, "read-only operation", summary)
        if tool.effect is ToolEffect.WRITE:
            return PolicyResult(
                PolicyDecision.REQUIRE_APPROVAL,
                "workspace modification requires approval",
                summary,
            )

        data = arguments.model_dump()
        argv = [str(item) for item in data.get("argv", [])]
        executable = _executable_name(argv[0]) if argv else ""
        raw_executable = _basename(argv[0]) if argv else ""
        if executable in _SHELL_EXECUTABLES or raw_executable.endswith(
            (".bat", ".cmd", ".ps1", ".sh")
        ):
            return PolicyResult(
                PolicyDecision.DENY,
                "shell interpreters are not allowed",
                summary,
            )
        if executable in _DESTRUCTIVE_EXECUTABLES or _is_destructive_git(argv):
            return PolicyResult(
                PolicyDecision.DENY,
                "destructive commands are not allowed",
                summary,
            )
        if executable == "git" and any(
            item == "-C"
            or item.startswith("--git-dir")
            or item.startswith("--work-tree")
            for item in argv[1:]
        ):
            return PolicyResult(
                PolicyDecision.DENY,
                "Git workspace override options are not allowed",
                summary,
            )
        operation_kind = _validation_command_kind(argv)
        if operation_kind is not None:
            return PolicyResult(
                PolicyDecision.ALLOW,
                "recognized validation command",
                summary,
                operation_kind,
            )
        return PolicyResult(
            PolicyDecision.REQUIRE_APPROVAL,
            "unrecognized command requires approval",
            summary,
        )
