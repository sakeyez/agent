from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from coding_agent.security import ApprovalStatus
from coding_agent.tools import ToolCall, ToolExecutionContext, ToolExecutor
from coding_agent.tools.builtin import create_coding_tool_registry
from coding_agent.workspace import WorkspaceContext


class Approver:
    def __init__(self, status: ApprovalStatus = ApprovalStatus.APPROVED) -> None:
        self.status = status
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        return self.status


def _init_git(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _run(tmp_path: Path, name: str, arguments: dict, *, approver=None, max_output=64 * 1024):
    return ToolExecutor(
        create_coding_tool_registry(),
        approval_provider=approver or Approver(),
        max_output_bytes=max_output,
    ).execute(
        ToolCall("call-1", name, arguments),
        ToolExecutionContext(WorkspaceContext.from_path(tmp_path), run_id="run-1"),
    )


def test_apply_patch_creates_updates_and_deletes_text_files(tmp_path: Path) -> None:
    _init_git(tmp_path)
    (tmp_path / "old.txt").write_text("old\n", encoding="utf-8")
    patch = """diff --git a/old.txt b/old.txt
--- a/old.txt
+++ b/old.txt
@@ -1 +1 @@
-old
+updated
diff --git a/new.txt b/new.txt
new file mode 100644
--- /dev/null
+++ b/new.txt
@@ -0,0 +1 @@
+created
"""
    applied = _run(tmp_path, "apply_patch", {"patch": patch})

    assert applied.is_error is False
    assert (tmp_path / "old.txt").read_text(encoding="utf-8") == "updated\n"
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "created\n"
    assert applied.metadata and set(applied.metadata["paths"]) == {"old.txt", "new.txt"}

    delete_patch = """diff --git a/old.txt b/old.txt
deleted file mode 100644
--- a/old.txt
+++ /dev/null
@@ -1 +0,0 @@
-updated
"""
    deleted = _run(tmp_path, "apply_patch", {"patch": delete_patch})
    assert deleted.is_error is False
    assert not (tmp_path / "old.txt").exists()


def test_apply_patch_rejects_invalid_second_file_without_partial_write(tmp_path: Path) -> None:
    _init_git(tmp_path)
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")
    patch = """--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-a
+changed
--- a/b.txt
+++ b/b.txt
@@ -1 +1 @@
-missing
+changed
"""

    result = _run(tmp_path, "apply_patch", {"patch": patch})

    assert result.error_code == "patch_rejected"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "a\n"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "b\n"


def test_apply_patch_does_not_treat_hunk_content_as_file_headers(tmp_path: Path) -> None:
    _init_git(tmp_path)
    (tmp_path / "markers.txt").write_text("-- old\n", encoding="utf-8")
    patch = """--- a/markers.txt
+++ b/markers.txt
@@ -1 +1 @@
--- old
+++ new
"""

    result = _run(tmp_path, "apply_patch", {"patch": patch})

    assert result.is_error is False
    assert result.metadata and result.metadata["paths"] == ["markers.txt"]
    assert (tmp_path / "markers.txt").read_text(encoding="utf-8") == "++ new\n"


def test_apply_patch_rejects_symlink_mode_patch(tmp_path: Path) -> None:
    _init_git(tmp_path)
    patch = """diff --git a/link b/link
new file mode 120000
--- /dev/null
+++ b/link
@@ -0,0 +1 @@
+../outside
"""
    result = _run(tmp_path, "apply_patch", {"patch": patch})
    assert result.error_code == "invalid_arguments"
    assert not (tmp_path / "link").exists()


@pytest.mark.parametrize(
    "path",
    ["../outside.txt", ".env", ".git/config", ".GIT/config", ".coding_agent/x"],
)
def test_apply_patch_rejects_unsafe_paths(tmp_path: Path, path: str) -> None:
    _init_git(tmp_path)
    patch = f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+x\n"
    result = _run(tmp_path, "apply_patch", {"patch": patch})
    assert result.error_code == "path_denied"


def test_apply_patch_rejects_symlink_target(tmp_path: Path) -> None:
    _init_git(tmp_path)
    target = tmp_path / "target.txt"
    target.write_text("old\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    patch = "--- a/link.txt\n+++ b/link.txt\n@@ -1 +1 @@\n-old\n+new\n"
    result = _run(tmp_path, "apply_patch", {"patch": patch})
    assert result.error_code == "path_denied"
    assert target.read_text(encoding="utf-8") == "old\n"


def test_run_command_success_nonzero_timeout_and_environment_sanitization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRIVATE_TEST_TOKEN", "must-not-leak")
    success = _run(
        tmp_path,
        "run_command",
        {"argv": [sys.executable, "-c", "import os; print(os.getenv('PRIVATE_TEST_TOKEN'))"]},
    )
    failed = _run(
        tmp_path,
        "run_command",
        {"argv": [sys.executable, "-c", "import sys; print('bad'); sys.exit(3)"]},
    )
    timed_out = _run(
        tmp_path,
        "run_command",
        {"argv": [sys.executable, "-c", "import time; time.sleep(5)"], "timeout_seconds": 1},
    )

    assert success.is_error is False and "None" in success.content
    assert "must-not-leak" not in success.content
    assert failed.error_code == "nonzero_exit" and failed.metadata["exit_code"] == 3
    assert timed_out.error_code == "timeout" and timed_out.metadata["timed_out"] is True


def test_run_command_policy_and_workspace_boundaries(tmp_path: Path) -> None:
    approver = Approver()
    denied = _run(
        tmp_path,
        "run_command",
        {"argv": ["powershell.exe", "-Command", "echo x"]},
        approver=approver,
    )
    outside = _run(
        tmp_path,
        "run_command",
        {"argv": [sys.executable, "-c", "print('x')"], "cwd": ".."},
    )

    assert denied.error_code == "policy_denied"
    assert approver.requests == []
    assert outside.error_code == "path_denied"


def test_git_diff_reports_staged_unstaged_and_untracked_changes(tmp_path: Path) -> None:
    _init_git(tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    tracked.write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    tracked.write_text("unstaged\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("new\n", encoding="utf-8")

    result = _run(tmp_path, "git_diff", {"mode": "all"})

    assert result.is_error is False
    assert "STATUS:" in result.content and "?? new.txt" in result.content
    assert "UNSTAGED DIFF:" in result.content and "+unstaged" in result.content
    assert "STAGED DIFF:" in result.content and "+staged" in result.content


def test_git_diff_reports_non_repository(tmp_path: Path) -> None:
    result = _run(tmp_path, "git_diff", {})
    assert result.error_code == "not_git_repository"
