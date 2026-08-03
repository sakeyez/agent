from __future__ import annotations

import os
from pathlib import Path

import pytest

from coding_agent.workspace import WorkspaceAccessError, WorkspaceContext, WorkspaceGuard


def _guard(root: Path) -> WorkspaceGuard:
    return WorkspaceGuard(WorkspaceContext.from_path(root))


def test_resolves_relative_and_inside_absolute_paths(tmp_path: Path) -> None:
    file_path = tmp_path / "src" / "app.py"
    file_path.parent.mkdir()
    file_path.write_text("pass\n", encoding="utf-8")
    guard = _guard(tmp_path)

    assert guard.resolve("src/app.py") == file_path.resolve()
    assert guard.resolve(file_path) == file_path.resolve()
    assert guard.relative(file_path) == "src/app.py"


@pytest.mark.parametrize("requested", ["../outside.txt", "src/../../outside.txt"])
def test_rejects_parent_traversal(tmp_path: Path, requested: str) -> None:
    with pytest.raises(WorkspaceAccessError, match="outside"):
        _guard(tmp_path).resolve(requested)


def test_rejects_absolute_path_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"

    with pytest.raises(WorkspaceAccessError, match="outside"):
        _guard(tmp_path).resolve(outside)


def test_rejects_symlink_escape_when_supported(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")

    with pytest.raises(WorkspaceAccessError, match="outside"):
        _guard(tmp_path).resolve("link.txt")


@pytest.mark.parametrize(
    "name",
    [".env", ".env.local", "private.pem", "id_rsa", "credentials.json"],
)
def test_rejects_sensitive_files(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_text("secret\n", encoding="utf-8")

    with pytest.raises(WorkspaceAccessError, match="sensitive"):
        _guard(tmp_path).resolve(name)


def test_allows_env_example(tmp_path: Path) -> None:
    example = tmp_path / ".env.example"
    example.write_text("TOKEN=example\n", encoding="utf-8")

    assert _guard(tmp_path).resolve(".env.example") == example.resolve()
