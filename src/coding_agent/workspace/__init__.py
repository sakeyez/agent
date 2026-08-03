"""Workspace context and filesystem boundary enforcement."""

from coding_agent.workspace.context import WorkspaceContext
from coding_agent.workspace.guard import WorkspaceAccessError, WorkspaceGuard

__all__ = ["WorkspaceAccessError", "WorkspaceContext", "WorkspaceGuard"]
