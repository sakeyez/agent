from __future__ import annotations

from pathlib import Path

from coding_agent.tools import ToolCall, ToolExecutionContext, ToolExecutor
from coding_agent.tools.builtin import create_readonly_tool_registry
from coding_agent.workspace import WorkspaceContext


def _run(tmp_path: Path, name: str, arguments: dict):
    registry = create_readonly_tool_registry()
    return ToolExecutor(registry).execute(
        ToolCall(call_id="call-1", name=name, arguments=arguments),
        ToolExecutionContext(workspace=WorkspaceContext.from_path(tmp_path)),
    )


def test_list_files_uses_relative_paths_glob_and_ignores_generated_directories(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "src" / "notes.txt").write_text("notes\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

    result = _run(tmp_path, "list_files", {"pattern": "*.py"})

    assert result.content.splitlines() == ["src/app.py"]
    assert ".env" not in result.content
    assert str(tmp_path) not in result.content


def test_read_file_returns_requested_numbered_lines(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = _run(
        tmp_path,
        "read_file",
        {"path": "sample.txt", "start_line": 2, "line_count": 2},
    )

    assert result.content == "2: two\n3: three"


def test_read_file_rejects_binary_large_and_sensitive_files(tmp_path: Path) -> None:
    (tmp_path / "binary.dat").write_bytes(b"a\x00b")
    (tmp_path / "large.txt").write_bytes(b"x" * (1024 * 1024 + 1))
    (tmp_path / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

    binary = _run(tmp_path, "read_file", {"path": "binary.dat"})
    large = _run(tmp_path, "read_file", {"path": "large.txt"})
    sensitive = _run(tmp_path, "read_file", {"path": ".env"})

    assert binary.is_error and "binary" in binary.content
    assert large.is_error and "1 MiB" in large.content
    assert sensitive.is_error and "sensitive" in sensitive.content


def test_search_text_is_literal_case_configurable_and_skips_sensitive_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.txt").write_text("Needle [literal]\nneedle elsewhere\n", encoding="utf-8")
    (tmp_path / ".env").write_text("needle secret\n", encoding="utf-8")

    insensitive = _run(tmp_path, "search_text", {"query": "needle"})
    sensitive = _run(
        tmp_path,
        "search_text",
        {"query": "Needle", "case_sensitive": True},
    )
    literal = _run(tmp_path, "search_text", {"query": "[literal]"})

    assert "a.txt:1" in insensitive.content and "a.txt:2" in insensitive.content
    assert ".env" not in insensitive.content
    assert "a.txt:1" in sensitive.content and "a.txt:2" not in sensitive.content
    assert "a.txt:1" in literal.content
