"""Workspace types: Workspace dataclass, WorkspaceStatus enum."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class WorkspaceStatus(str, Enum):
    """Workspace lifecycle states."""

    READY = "ready"       # worktree created, available for use
    LOCKED = "locked"     # actively held by a session
    DIRTY = "dirty"       # has uncommitted changes
    REMOVED = "removed"   # cleaned up / deleted


@dataclass
class Workspace:
    """A session-scoped git worktree workspace.

    Each Hermes session gets its own isolated worktree so agents can
    work without interfering with each other or the user's main checkout.
    """

    id: str                          # workspace ID (= session ID)
    repo_path: str                   # original repo root path
    base_branch: str                 # branch the worktree was forked from
    worktree_path: str               # absolute path of the git worktree
    branch_name: str                 # the new branch created for this worktree
    status: WorkspaceStatus = WorkspaceStatus.READY
    created_at: str = ""             # ISO-8601 timestamp
    locked_by_session_id: str | None = None  # session that currently holds the lock


@dataclass
class DiffEntry:
    """A single file diff entry returned by get_diff."""

    path: str             # relative file path
    additions: int = 0    # lines added
    deletions: int = 0    # lines removed
