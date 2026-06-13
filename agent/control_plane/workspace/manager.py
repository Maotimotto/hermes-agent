"""WorkspaceManager: core class for session-scoped git worktree management.

Lifecycle:
    1. create_workspace()  → git worktree add + branch + lock + persist
    2. get_workspace()     → read metadata
    3. get_diff()          → delegate to git_ops
    4. cleanup()           → unlock + git worktree remove + status=removed

Storage is injected via the WorkspaceStore protocol (duck-typed).
Tests can provide a simple in-memory mock.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from .git_ops import (
    create_worktree,
    get_diff,
    is_clean,
    remove_worktree,
    validate_branch,
    validate_repo,
)
from .lock import WorkspaceLock
from .types import DiffEntry, Workspace, WorkspaceStatus


# ── Storage protocol ─────────────────────────────────────────────────

@runtime_checkable
class WorkspaceStore(Protocol):
    """Minimal persistence contract for workspace metadata.

    Implementations may use SQLite, a dict, or anything else.
    The WorkspaceManager only depends on this interface.
    """

    async def save_workspace(self, workspace: Workspace) -> None: ...

    async def get_workspace(self, workspace_id: str) -> Workspace | None: ...

    async def list_workspaces(
        self,
        *,
        status: WorkspaceStatus | None = None,
        repo_path: str | None = None,
    ) -> list[Workspace]: ...

    async def update_workspace_status(
        self,
        workspace_id: str,
        status: WorkspaceStatus,
        locked_by_session_id: str | None = None,
    ) -> None: ...


# ── Default in-memory store (for tests & simple usage) ───────────────

class InMemoryWorkspaceStore:
    """A trivial dict-backed store.  Satisfies WorkspaceStore."""

    def __init__(self) -> None:
        self._workspaces: dict[str, Workspace] = {}

    async def save_workspace(self, workspace: Workspace) -> None:
        self._workspaces[workspace.id] = workspace

    async def get_workspace(self, workspace_id: str) -> Workspace | None:
        return self._workspaces.get(workspace_id)

    async def list_workspaces(
        self,
        *,
        status: WorkspaceStatus | None = None,
        repo_path: str | None = None,
    ) -> list[Workspace]:
        result = list(self._workspaces.values())
        if status is not None:
            result = [w for w in result if w.status == status]
        if repo_path is not None:
            result = [w for w in result if w.repo_path == repo_path]
        return result

    async def update_workspace_status(
        self,
        workspace_id: str,
        status: WorkspaceStatus,
        locked_by_session_id: str | None = None,
    ) -> None:
        ws = self._workspaces.get(workspace_id)
        if ws is not None:
            ws.status = status
            ws.locked_by_session_id = locked_by_session_id


# ── WorkspaceManager ─────────────────────────────────────────────────

class WorkspaceManager:
    """Manages session-scoped git worktrees.

    Args:
        store: Any object satisfying WorkspaceStore protocol.
        workspaces_root: Base directory for worktrees.
                         Defaults to ~/.hermes/workspaces/<workspace_id>/
    """

    def __init__(
        self,
        store: WorkspaceStore | None = None,
        workspaces_root: Path | None = None,
    ) -> None:
        self._store: WorkspaceStore = store or InMemoryWorkspaceStore()
        self._workspaces_root = workspaces_root or (
            Path.home() / ".hermes" / "workspaces"
        )
        # In-process lock cache: workspace_id → WorkspaceLock
        self._locks: dict[str, WorkspaceLock] = {}

    # ── create ───────────────────────────────────────────────────────

    async def create_workspace(
        self,
        repo_path: str | Path,
        base_branch: str,
        session_id: str,
    ) -> Workspace:
        """Create a new workspace for *session_id*.

        Steps:
            1. Validate repo and branch.
            2. Generate workspace ID and branch name.
            3. Create git worktree at ~/.hermes/workspaces/<id>/
            4. Acquire lock.
            5. Persist metadata in store.

        Returns the newly created Workspace.

        Raises:
            ValueError: repo or branch validation failed.
            RuntimeError: worktree creation or lock acquisition failed.
        """
        repo = Path(repo_path).resolve()

        # 1. Validate
        await validate_repo(repo)
        await validate_branch(repo, base_branch)

        # 2. Generate IDs
        ws_id = uuid.uuid4().hex[:12]
        branch_name = f"hermes/{session_id}/{ws_id}"
        worktree_path = self._workspaces_root / ws_id

        # 3. Create worktree
        await create_worktree(repo, base_branch, worktree_path, branch_name)

        # 4. Lock
        lock = WorkspaceLock(ws_id)
        if not await lock.acquire(session_id):
            # Roll back worktree
            await _safe_remove_worktree(worktree_path)
            raise RuntimeError(
                f"Failed to acquire lock for workspace {ws_id} "
                f"(session {session_id})"
            )
        self._locks[ws_id] = lock

        # 5. Persist
        now = datetime.now(timezone.utc).isoformat()
        workspace = Workspace(
            id=ws_id,
            repo_path=str(repo),
            base_branch=base_branch,
            worktree_path=str(worktree_path),
            branch_name=branch_name,
            status=WorkspaceStatus.LOCKED,
            created_at=now,
            locked_by_session_id=session_id,
        )
        await self._store.save_workspace(workspace)
        return workspace

    # ── read ─────────────────────────────────────────────────────────

    async def get_workspace(self, workspace_id: str) -> Workspace | None:
        """Get workspace metadata by ID."""
        return await self._store.get_workspace(workspace_id)

    async def list_workspaces(
        self,
        *,
        status: WorkspaceStatus | None = None,
        repo_path: str | None = None,
    ) -> list[Workspace]:
        """List workspaces, optionally filtered."""
        return await self._store.list_workspaces(status=status, repo_path=repo_path)

    # ── diff ─────────────────────────────────────────────────────────

    async def get_diff(self, workspace_id: str) -> list[DiffEntry]:
        """Get diff summary for a workspace.

        Delegates to git_ops.get_diff.

        Raises:
            KeyError: workspace not found.
        """
        ws = await self._store.get_workspace(workspace_id)
        if ws is None:
            raise KeyError(f"Workspace not found: {workspace_id}")
        return await get_diff(Path(ws.worktree_path))

    async def is_clean(self, workspace_id: str) -> bool:
        """Check if workspace has no uncommitted changes.

        Raises:
            KeyError: workspace not found.
        """
        ws = await self._store.get_workspace(workspace_id)
        if ws is None:
            raise KeyError(f"Workspace not found: {workspace_id}")
        return await is_clean(Path(ws.worktree_path))

    # ── cleanup ──────────────────────────────────────────────────────

    async def cleanup(
        self,
        workspace_id: str,
        *,
        force: bool = False,
    ) -> None:
        """Release lock, remove worktree, mark as removed.

        Args:
            workspace_id: ID of workspace to clean up.
            force: If True, force-remove the worktree even if dirty.

        Raises:
            KeyError: workspace not found.
        """
        ws = await self._store.get_workspace(workspace_id)
        if ws is None:
            raise KeyError(f"Workspace not found: {workspace_id}")

        # 1. Release lock
        lock = self._locks.get(workspace_id)
        if lock is not None:
            await lock.release(ws.locked_by_session_id or "")
            self._locks.pop(workspace_id, None)

        # 2. Remove worktree
        worktree_path = Path(ws.worktree_path)
        if worktree_path.exists():
            await _safe_remove_worktree(worktree_path, force=force)

        # 3. Update store
        await self._store.update_workspace_status(
            workspace_id,
            WorkspaceStatus.REMOVED,
            locked_by_session_id=None,
        )


# ── internal helpers ─────────────────────────────────────────────────

async def _safe_remove_worktree(path: Path, *, force: bool = True) -> None:
    """Attempt to remove a worktree, swallowing errors."""
    try:
        await remove_worktree(path, force=force)
    except RuntimeError:
        # Last resort: remove the directory directly
        import shutil
        shutil.rmtree(str(path), ignore_errors=True)
