"""Tests for WorkspaceManager: creation, diff, cleanup, and lock concurrency.

All tests use pytest-asyncio and tmp_path fixtures to create ephemeral git repos.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.control_plane.workspace.git_ops import (
    create_worktree,
    get_diff,
    get_name_status,
    get_unified_diff,
    is_clean,
    remove_worktree,
    validate_repo,
)
from agent.control_plane.workspace.lock import WorkspaceLock
from agent.control_plane.workspace.manager import (
    InMemoryWorkspaceStore,
    WorkspaceManager,
)
from agent.control_plane.workspace.types import Workspace, WorkspaceStatus


# ── helpers ──────────────────────────────────────────────────────────

async def _run_git(*args: str, cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a git command in a subprocess."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(), stderr.decode()


async def _init_repo(repo_path: Path) -> None:
    """Create a minimal git repo with one commit on 'main'."""
    repo_path.mkdir(parents=True, exist_ok=True)
    await _run_git("init", "-b", "main", cwd=repo_path)
    await _run_git("config", "user.email", "test@hermes.local", cwd=repo_path)
    await _run_git("config", "user.name", "Hermes Test", cwd=repo_path)
    # Create an initial commit so HEAD exists
    (repo_path / "README.md").write_text("initial\n")
    await _run_git("add", ".", cwd=repo_path)
    await _run_git("commit", "-m", "init", cwd=repo_path)


# ── git_ops unit tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_validate_repo_valid(tmp_path: Path) -> None:
    """validate_repo should pass for a real git repo."""
    repo = tmp_path / "repo"
    await _init_repo(repo)
    # Should not raise
    await validate_repo(repo)


@pytest.mark.asyncio
async def test_validate_repo_invalid(tmp_path: Path) -> None:
    """validate_repo should raise ValueError for a non-repo dir."""
    not_repo = tmp_path / "not_a_repo"
    not_repo.mkdir()
    with pytest.raises(ValueError, match="Not a git repository"):
        await validate_repo(not_repo)


@pytest.mark.asyncio
async def test_create_and_remove_worktree(tmp_path: Path) -> None:
    """create_worktree should produce a valid worktree; remove_worktree should clean it."""
    repo = tmp_path / "repo"
    await _init_repo(repo)

    wt_path = tmp_path / "worktree_test"
    await create_worktree(repo, "main", wt_path, "test-branch")

    assert wt_path.exists()
    assert (wt_path / ".git").exists()
    assert (wt_path / "README.md").read_text() == "initial\n"

    await remove_worktree(wt_path, force=True)
    assert not wt_path.exists()


@pytest.mark.asyncio
async def test_is_clean_after_init(tmp_path: Path) -> None:
    """A freshly created worktree should be clean."""
    repo = tmp_path / "repo"
    await _init_repo(repo)

    wt_path = tmp_path / "wt_clean"
    await create_worktree(repo, "main", wt_path, "clean-branch")
    assert await is_clean(wt_path) is True

    # Dirty it
    (wt_path / "new_file.txt").write_text("dirty\n")
    assert await is_clean(wt_path) is False


@pytest.mark.asyncio
async def test_get_diff_after_modification(tmp_path: Path) -> None:
    """get_diff should return additions/deletions after modifying files."""
    repo = tmp_path / "repo"
    await _init_repo(repo)

    wt_path = tmp_path / "wt_diff"
    await create_worktree(repo, "main", wt_path, "diff-branch")

    # Modify an existing file
    (wt_path / "README.md").write_text("modified content\n")
    # Add a new file
    (wt_path / "new.py").write_text("print('hello')\n")
    # Stage so diff --numstat works against HEAD
    await _run_git("add", ".", cwd=wt_path)

    diffs = await get_diff(wt_path)
    assert len(diffs) >= 2  # README.md and new.py

    paths = {d.path for d in diffs}
    assert "README.md" in paths
    assert "new.py" in paths

    # Verify additions are counted
    for d in diffs:
        if d.path == "new.py":
            assert d.additions >= 1


# ── lock concurrency test ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lock_concurrent_acquisition(tmp_path: Path) -> None:
    """Second acquire() on the same workspace must return False.

    This tests the core concurrency guarantee: one workspace = one session.
    """
    locks_dir = tmp_path / "locks"
    lock_a = WorkspaceLock("ws_concurrent", locks_dir=locks_dir)
    lock_b = WorkspaceLock("ws_concurrent", locks_dir=locks_dir)

    # First acquire succeeds
    ok_a = await lock_a.acquire("session_alpha", ttl=60)
    assert ok_a is True

    # Second acquire from a different session fails
    ok_b = await lock_b.acquire("session_beta", ttl=60)
    assert ok_b is False

    # Release by the owner
    released = await lock_a.release("session_alpha")
    assert released is True

    # Now session_beta can acquire
    ok_b2 = await lock_b.acquire("session_beta", ttl=60)
    assert ok_b2 is True
    await lock_b.release("session_beta")


@pytest.mark.asyncio
async def test_lock_release_by_wrong_session(tmp_path: Path) -> None:
    """release() should refuse if caller is not the lock holder."""
    locks_dir = tmp_path / "locks"
    lock = WorkspaceLock("ws_wrong_release", locks_dir=locks_dir)

    await lock.acquire("session_owner", ttl=60)

    # A different session tries to release
    released = await lock.release("session_intruder")
    assert released is False

    # The real owner can still release
    released = await lock.release("session_owner")
    assert released is True


# ── WorkspaceManager integration tests ───────────────────────────────

@pytest.mark.asyncio
async def test_manager_create_and_get(tmp_path: Path) -> None:
    """WorkspaceManager.create_workspace should produce a valid worktree and metadata."""
    repo = tmp_path / "repo"
    await _init_repo(repo)

    # Use a temp root so worktrees don't pollute ~/.hermes
    ws_root = tmp_path / "workspaces"
    store = InMemoryWorkspaceStore()
    mgr = WorkspaceManager(store=store, workspaces_root=ws_root)

    ws = await mgr.create_workspace(repo, "main", "sess_001")

    assert isinstance(ws, Workspace)
    assert ws.status == WorkspaceStatus.LOCKED
    assert ws.locked_by_session_id == "sess_001"
    assert ws.repo_path == str(repo.resolve())
    assert Path(ws.worktree_path).exists()

    # get_workspace returns the same object
    ws2 = await mgr.get_workspace(ws.id)
    assert ws2 is not None
    assert ws2.id == ws.id

    # list_workspaces should include it
    all_ws = await mgr.list_workspaces()
    assert len(all_ws) == 1
    assert all_ws[0].id == ws.id


@pytest.mark.asyncio
async def test_manager_get_diff(tmp_path: Path) -> None:
    """WorkspaceManager.get_diff should delegate to git_ops and return entries."""
    repo = tmp_path / "repo"
    await _init_repo(repo)

    ws_root = tmp_path / "workspaces"
    mgr = WorkspaceManager(workspaces_root=ws_root)

    ws = await mgr.create_workspace(repo, "main", "sess_diff")

    # Make a change in the worktree
    wt = Path(ws.worktree_path)
    (wt / "agent_work.py").write_text("x = 42\n")
    await _run_git("add", ".", cwd=wt)

    diffs = await mgr.get_diff(ws.id)
    assert any(d.path == "agent_work.py" for d in diffs)


@pytest.mark.asyncio
async def test_manager_is_clean(tmp_path: Path) -> None:
    """is_clean should reflect worktree state."""
    repo = tmp_path / "repo"
    await _init_repo(repo)

    ws_root = tmp_path / "workspaces"
    mgr = WorkspaceManager(workspaces_root=ws_root)

    ws = await mgr.create_workspace(repo, "main", "sess_clean")
    assert await mgr.is_clean(ws.id) is True

    # Make it dirty
    wt = Path(ws.worktree_path)
    (wt / "dirty.txt").write_text("oops\n")
    assert await mgr.is_clean(ws.id) is False


@pytest.mark.asyncio
async def test_manager_cleanup(tmp_path: Path) -> None:
    """cleanup should remove the worktree and set status to REMOVED."""
    repo = tmp_path / "repo"
    await _init_repo(repo)

    ws_root = tmp_path / "workspaces"
    mgr = WorkspaceManager(workspaces_root=ws_root)

    ws = await mgr.create_workspace(repo, "main", "sess_cleanup")
    wt_path = Path(ws.worktree_path)
    assert wt_path.exists()

    await mgr.cleanup(ws.id, force=True)

    # Worktree directory should be gone
    assert not wt_path.exists()

    # Status should be REMOVED
    ws_after = await mgr.get_workspace(ws.id)
    assert ws_after is not None
    assert ws_after.status == WorkspaceStatus.REMOVED
    assert ws_after.locked_by_session_id is None


@pytest.mark.asyncio
async def test_manager_cleanup_nonexistent(tmp_path: Path) -> None:
    """cleanup on a nonexistent workspace should raise KeyError."""
    mgr = WorkspaceManager(workspaces_root=tmp_path / "ws")
    with pytest.raises(KeyError):
        await mgr.cleanup("nonexistent_id")


@pytest.mark.asyncio
async def test_manager_create_with_invalid_repo(tmp_path: Path) -> None:
    """create_workspace should reject a non-git directory."""
    not_repo = tmp_path / "not_repo"
    not_repo.mkdir()

    mgr = WorkspaceManager(workspaces_root=tmp_path / "ws")
    with pytest.raises(ValueError, match="Not a git repository"):
        await mgr.create_workspace(not_repo, "main", "sess_bad")


@pytest.mark.asyncio
async def test_manager_create_with_invalid_branch(tmp_path: Path) -> None:
    """create_workspace should reject a nonexistent base branch."""
    repo = tmp_path / "repo"
    await _init_repo(repo)

    mgr = WorkspaceManager(workspaces_root=tmp_path / "ws")
    with pytest.raises(ValueError, match="Branch does not exist"):
        await mgr.create_workspace(repo, "nonexistent_branch", "sess_bad_branch")


@pytest.mark.asyncio
async def test_manager_list_with_filters(tmp_path: Path) -> None:
    """list_workspaces should support status and repo_path filters."""
    repo1 = tmp_path / "repo1"
    repo2 = tmp_path / "repo2"
    await _init_repo(repo1)
    await _init_repo(repo2)

    ws_root = tmp_path / "workspaces"
    mgr = WorkspaceManager(workspaces_root=ws_root)

    ws1 = await mgr.create_workspace(repo1, "main", "s1")
    ws2 = await mgr.create_workspace(repo2, "main", "s2")

    # Filter by repo_path
    result = await mgr.list_workspaces(repo_path=str(repo1.resolve()))
    assert len(result) == 1
    assert result[0].id == ws1.id

    # Filter by status
    result = await mgr.list_workspaces(status=WorkspaceStatus.LOCKED)
    assert len(result) == 2

    # Cleanup one and filter
    await mgr.cleanup(ws1.id, force=True)
    result_locked = await mgr.list_workspaces(status=WorkspaceStatus.LOCKED)
    assert len(result_locked) == 1
    assert result_locked[0].id == ws2.id

    result_removed = await mgr.list_workspaces(status=WorkspaceStatus.REMOVED)
    assert len(result_removed) == 1
    assert result_removed[0].id == ws1.id


# ── get_name_status (W7.5 — Claude file change tracking) ────────────────


@pytest.mark.asyncio
async def test_get_name_status_modified_file(tmp_path: Path) -> None:
    """A modified tracked file shows up with status M."""
    repo = tmp_path / "repo"
    await _init_repo(repo)
    (repo / "README.md").write_text("initial\nmore\n")

    entries = await get_name_status(repo)
    assert ("M", "README.md") in entries


@pytest.mark.asyncio
async def test_get_name_status_added_untracked(tmp_path: Path) -> None:
    """A brand-new untracked file shows up with status A via ls-files probe."""
    repo = tmp_path / "repo"
    await _init_repo(repo)
    (repo / "newfile.py").write_text("print(hi)\n")

    entries = await get_name_status(repo)
    assert ("A", "newfile.py") in entries


@pytest.mark.asyncio
async def test_get_name_status_added_staged(tmp_path: Path) -> None:
    """A staged add (git diff sees it but not yet committed) shows up as A."""
    repo = tmp_path / "repo"
    await _init_repo(repo)
    (repo / "added.txt").write_text("hello\n")
    await _run_git("add", "added.txt", cwd=repo)

    entries = await get_name_status(repo)
    assert ("A", "added.txt") in entries


@pytest.mark.asyncio
async def test_get_name_status_deleted_file(tmp_path: Path) -> None:
    """A deleted tracked file shows up with status D."""
    repo = tmp_path / "repo"
    await _init_repo(repo)
    (repo / "README.md").unlink()

    entries = await get_name_status(repo)
    assert ("D", "README.md") in entries


@pytest.mark.asyncio
async def test_get_name_status_no_changes(tmp_path: Path) -> None:
    """Clean repo returns an empty list."""
    repo = tmp_path / "repo"
    await _init_repo(repo)

    entries = await get_name_status(repo)
    assert entries == []


@pytest.mark.asyncio
async def test_get_name_status_dedupes_tracked_over_untracked(tmp_path: Path) -> None:
    """Tracked status takes precedence over the untracked probe (no double-count)."""
    repo = tmp_path / "repo"
    await _init_repo(repo)
    # Stage a new file so it appears in `git diff --cached`-style flow,
    # but ls-files --others would also surface it before staging.
    (repo / "staged.txt").write_text("x\n")
    await _run_git("add", "staged.txt", cwd=repo)

    entries = await get_name_status(repo)
    paths = [p for _s, p in entries]
    assert paths.count("staged.txt") == 1


# ── unified_diff tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_unified_diff_modified_file(tmp_path: Path) -> None:
    """Modified tracked file produces a unified diff with +/- markers."""
    repo = tmp_path / "repo"
    await _init_repo(repo)
    # README.md exists; modify it
    (repo / "README.md").write_text("initial\nsecond line\n")

    diffs = await get_unified_diff(repo)
    assert "README.md" in diffs
    body = diffs["README.md"]
    assert "diff --git" in body
    assert "+second line" in body


@pytest.mark.asyncio
async def test_get_unified_diff_untracked_file(tmp_path: Path) -> None:
    """Untracked file is diffed against /dev/null."""
    repo = tmp_path / "repo"
    await _init_repo(repo)
    (repo / "new_file.py").write_text("print('hello')\n")

    diffs = await get_unified_diff(repo)
    assert "new_file.py" in diffs
    body = diffs["new_file.py"]
    # /dev/null diff still emits a diff header and a + line
    assert "+print('hello')" in body


@pytest.mark.asyncio
async def test_get_unified_diff_explicit_paths_filter(tmp_path: Path) -> None:
    """Passing paths= restricts the result to that subset."""
    repo = tmp_path / "repo"
    await _init_repo(repo)
    (repo / "a.txt").write_text("a\n")
    (repo / "b.txt").write_text("b\n")

    diffs = await get_unified_diff(repo, paths=["a.txt"])
    assert list(diffs.keys()) == ["a.txt"]


@pytest.mark.asyncio
async def test_get_unified_diff_no_changes(tmp_path: Path) -> None:
    """Clean repo returns an empty dict."""
    repo = tmp_path / "repo"
    await _init_repo(repo)

    diffs = await get_unified_diff(repo)
    assert diffs == {}


@pytest.mark.asyncio
async def test_get_unified_diff_deleted_file(tmp_path: Path) -> None:
    """Deleted tracked file shows up as a removal diff."""
    repo = tmp_path / "repo"
    await _init_repo(repo)
    (repo / "README.md").unlink()

    diffs = await get_unified_diff(repo)
    assert "README.md" in diffs
    assert "-initial" in diffs["README.md"]


@pytest.mark.asyncio
async def test_manager_get_unified_diff(tmp_path: Path) -> None:
    """WorkspaceManager.get_unified_diff delegates to git_ops correctly."""
    repo = tmp_path / "repo"
    await _init_repo(repo)

    ws_root = tmp_path / "workspaces"
    mgr = WorkspaceManager(workspaces_root=ws_root)

    ws = await mgr.create_workspace(repo, "main", "sess_unified")
    wt = Path(ws.worktree_path)
    (wt / "README.md").write_text("initial\nchanged\n")
    (wt / "added.py").write_text("x = 1\n")

    diffs = await mgr.get_unified_diff(ws.id)
    assert "README.md" in diffs
    assert "added.py" in diffs
    assert "+changed" in diffs["README.md"]
    assert "+x = 1" in diffs["added.py"]


@pytest.mark.asyncio
async def test_manager_get_name_status(tmp_path: Path) -> None:
    """WorkspaceManager.get_name_status returns ``[(status, path), ...]``."""
    repo = tmp_path / "repo"
    await _init_repo(repo)

    ws_root = tmp_path / "workspaces"
    mgr = WorkspaceManager(workspaces_root=ws_root)

    ws = await mgr.create_workspace(repo, "main", "sess_ns")
    wt = Path(ws.worktree_path)
    (wt / "added.py").write_text("x = 1\n")
    (wt / "README.md").write_text("initial\nupdated\n")

    entries = await mgr.get_name_status(ws.id)
    by_path = {p: s for s, p in entries}
    assert by_path.get("added.py") == "A"
    assert by_path.get("README.md") == "M"


@pytest.mark.asyncio
async def test_get_unified_diff_unknown_paths_silently_skipped(
    tmp_path: Path,
) -> None:
    """Paths that don't exist are silently skipped, not raised."""
    repo = tmp_path / "repo"
    await _init_repo(repo)

    diffs = await get_unified_diff(repo, paths=["does_not_exist.txt"])
    assert diffs == {}
