"""Workspace Manager — session-scoped git worktree management.

Provides:
    - types: Workspace, WorkspaceStatus, DiffEntry
    - git_ops: async git worktree operations
    - lock: file-based advisory locking
    - manager: WorkspaceManager core class
"""

from .manager import InMemoryWorkspaceStore, WorkspaceManager, WorkspaceStore
from .types import DiffEntry, Workspace, WorkspaceStatus

__all__ = [
    "DiffEntry",
    "InMemoryWorkspaceStore",
    "Workspace",
    "WorkspaceManager",
    "WorkspaceStatus",
    "WorkspaceStore",
]
