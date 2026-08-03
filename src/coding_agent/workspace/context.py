"""Immutable context describing the active workspace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    """Resolved workspace root supplied to every filesystem tool."""

    root: Path

    @classmethod
    def from_path(cls, path: str | Path) -> "WorkspaceContext":
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("workspace must be an existing directory")
        return cls(root=root)
