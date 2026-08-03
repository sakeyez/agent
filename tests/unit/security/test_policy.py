from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.security import DefaultOperationPolicy, PolicyDecision
from coding_agent.tools import ToolExecutionContext
from coding_agent.tools.builtin import create_coding_tool_registry
from coding_agent.workspace import WorkspaceContext


def _decision(tmp_path: Path, tool_name: str, arguments: dict) -> PolicyDecision:
    tool = create_coding_tool_registry().get(tool_name)
    assert tool is not None
    validated = tool.args_schema.model_validate(arguments)
    result = DefaultOperationPolicy().evaluate(
        tool,
        validated,
        ToolExecutionContext(WorkspaceContext.from_path(tmp_path)),
    )
    return result.decision


@pytest.mark.parametrize("tool_name", ["list_files", "read_file", "search_text", "git_diff"])
def test_read_tools_are_automatically_allowed(tmp_path: Path, tool_name: str) -> None:
    arguments = {"path": "x"} if tool_name == "read_file" else {}
    if tool_name == "search_text":
        arguments = {"query": "x"}
    assert _decision(tmp_path, tool_name, arguments) is PolicyDecision.ALLOW


@pytest.mark.parametrize(
    "argv",
    [
        ["pytest", "-q"],
        ["python", "-m", "pytest"],
        ["uv", "run", "pytest"],
        ["ruff", "check", "."],
        ["mypy", "src"],
        ["pyright"],
    ],
)
def test_known_validation_commands_are_allowed(tmp_path: Path, argv: list[str]) -> None:
    assert _decision(tmp_path, "run_command", {"argv": argv}) is PolicyDecision.ALLOW


def test_writes_and_unknown_commands_require_approval(tmp_path: Path) -> None:
    patch = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b\n"
    assert _decision(tmp_path, "apply_patch", {"patch": patch}) is PolicyDecision.REQUIRE_APPROVAL
    assert _decision(tmp_path, "run_command", {"argv": ["npm", "test"]}) is PolicyDecision.REQUIRE_APPROVAL
    assert _decision(tmp_path, "run_command", {"argv": ["ruff", "check", "--fix", "."]}) is PolicyDecision.REQUIRE_APPROVAL


@pytest.mark.parametrize(
    "argv",
    [
        ["powershell.exe", "-Command", "echo x"],
        ["script.cmd"],
        ["C:/Windows/System32/shutdown.exe", "/s"],
        ["rm", "-rf", "src"],
        ["git", "clean", "-fd"],
        ["git", "reset", "--hard"],
        ["git", "checkout", "--", "file.txt"],
        ["git", "restore", "file.txt"],
        ["git", "-C", "../other", "clean", "-fd"],
    ],
)
def test_shell_and_destructive_commands_are_denied(tmp_path: Path, argv: list[str]) -> None:
    assert _decision(tmp_path, "run_command", {"argv": argv}) is PolicyDecision.DENY
