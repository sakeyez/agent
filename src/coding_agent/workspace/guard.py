"""Boundary checks for workspace filesystem access."""

from __future__ import annotations

from pathlib import Path

from coding_agent.workspace.context import WorkspaceContext

IGNORED_DIRECTORY_NAMES = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", ".coding_agent"}
)
_SENSITIVE_EXACT_NAMES = frozenset(
    {
        ".npmrc",
        ".pypirc",
        "credentials.json",
        "id_dsa",
        "id_ed25519",
        "id_ecdsa",
        "id_rsa",
        "service-account.json",
    }
)
_SENSITIVE_SUFFIXES = frozenset({".key", ".p12", ".pfx", ".pem"})


class WorkspaceAccessError(ValueError):
    """A safe-to-display workspace access failure."""


class WorkspaceGuard:
    """Resolve paths without allowing access outside the active workspace."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context

    def resolve(self, requested_path: str | Path, *, allow_sensitive: bool = False) -> Path:
        requested = Path(requested_path)
        candidate = requested if requested.is_absolute() else self.context.root / requested
        resolved = candidate.expanduser().resolve(strict=False)
        try:
            resolved.relative_to(self.context.root)
        except ValueError as error:
            raise WorkspaceAccessError("path is outside the active workspace") from error

        if not allow_sensitive and self.is_sensitive(resolved):
            raise WorkspaceAccessError("access to sensitive files is not allowed")
        return resolved

    def relative(self, path: Path) -> str:
        """Return a stable POSIX-style path without leaking the absolute root."""

        try:
            relative = path.resolve(strict=False).relative_to(self.context.root)
        except ValueError as error:
            raise WorkspaceAccessError("path is outside the active workspace") from error
        value = relative.as_posix()
        return value if value else "."

    def resolve_for_write(self, requested_path: str | Path) -> Path:
        """Resolve a writable path while rejecting protected and symlinked locations."""

        requested = Path(requested_path)
        if requested.is_absolute():
            raise WorkspaceAccessError("write paths must be workspace-relative")
        candidate = self.context.root / requested
        current = self.context.root
        for part in requested.parts:
            if part in {"", "."}:
                continue
            current = current / part
            if current.is_symlink():
                raise WorkspaceAccessError("writes through symbolic links are not allowed")

        resolved = candidate.resolve(strict=False)
        try:
            relative = resolved.relative_to(self.context.root)
        except ValueError as error:
            raise WorkspaceAccessError("path is outside the active workspace") from error
        if any(part.lower() in IGNORED_DIRECTORY_NAMES for part in relative.parts):
            raise WorkspaceAccessError("writes to protected workspace directories are not allowed")
        if self.is_sensitive(resolved):
            raise WorkspaceAccessError("access to sensitive files is not allowed")
        return resolved

    def is_sensitive(self, path: Path) -> bool:
        """Identify files that should never be copied into model context."""

        name = path.name.lower()
        if name == ".env.example":
            return False
        if name == ".env" or name.startswith(".env."):
            return True
        if name in _SENSITIVE_EXACT_NAMES or path.suffix.lower() in _SENSITIVE_SUFFIXES:
            return True
        return name.startswith("service-account") and name.endswith(".json")

    @staticmethod
    def is_ignored_directory(path: Path) -> bool:
        return path.name in IGNORED_DIRECTORY_NAMES
