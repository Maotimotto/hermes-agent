"""Wave 7.5 — Claude SDK runtime file change tracking.

Targets the post-turn `_scan_file_changes` helper:
- emits one FileChangedEvent per modified path detected via
  `git diff --name-status HEAD` + the untracked probe;
- maps git status letters to operation enum (A/C/?→create, D→delete, else→edit);
- swallows errors when worktree_path is missing or not a git repo.

We don't spin up the real Claude SDK here — `_scan_file_changes` is
self-contained and exercised directly with a temp git repo.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.control_plane.hermes_event import FileChangedEvent
from agent.control_plane.runtimes.claude_agent_sdk_runtime import (
    ClaudeAgentSdkRuntime,
)


# ── helpers ──────────────────────────────────────────────────────────


async def _run_git(*args: str, cwd: Path | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(), stderr.decode()


async def _init_repo(repo_path: Path) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    await _run_git("init", "-b", "main", cwd=repo_path)
    await _run_git("config", "user.email", "test@hermes.local", cwd=repo_path)
    await _run_git("config", "user.name", "Hermes Test", cwd=repo_path)
    (repo_path / "README.md").write_text("initial\n")
    await _run_git("add", ".", cwd=repo_path)
    await _run_git("commit", "-m", "init", cwd=repo_path)


def _make_runtime() -> ClaudeAgentSdkRuntime:
    """Construct a runtime with the bare minimum config — we never call the SDK."""
    return ClaudeAgentSdkRuntime(
        config={"api_key": "sk-test-not-used", "model": "claude-test"},
    )


async def _collect(
    runtime: ClaudeAgentSdkRuntime,
    *,
    worktree_path: str | None,
    starting_seq: int = 0,
) -> list[FileChangedEvent]:
    out: list[FileChangedEvent] = []
    async for ev in runtime._scan_file_changes(
        session_id="sess-test",
        turn_id="turn-test",
        worktree_path=worktree_path,
        starting_seq=starting_seq,
    ):
        out.append(ev)
    return out


# ── tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_file_changes_emits_create_for_untracked(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    await _init_repo(repo)
    (repo / "new.py").write_text("print('hello')\n")

    runtime = _make_runtime()
    events = await _collect(runtime, worktree_path=str(repo))

    paths = {(ev.path, ev.operation) for ev in events}
    assert ("new.py", "create") in paths
    # session/turn ids are propagated.
    assert all(ev.session_id == "sess-test" for ev in events)
    assert all(ev.turn_id == "turn-test" for ev in events)


@pytest.mark.asyncio
async def test_scan_file_changes_emits_edit_for_modified(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    await _init_repo(repo)
    (repo / "README.md").write_text("changed\n")

    runtime = _make_runtime()
    events = await _collect(runtime, worktree_path=str(repo))

    assert any(
        ev.path == "README.md" and ev.operation == "edit" for ev in events
    )


@pytest.mark.asyncio
async def test_scan_file_changes_emits_delete(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    await _init_repo(repo)
    (repo / "README.md").unlink()

    runtime = _make_runtime()
    events = await _collect(runtime, worktree_path=str(repo))

    assert any(
        ev.path == "README.md" and ev.operation == "delete" for ev in events
    )


@pytest.mark.asyncio
async def test_scan_file_changes_assigns_sequential_seq(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    await _init_repo(repo)
    (repo / "a.py").write_text("a\n")
    (repo / "b.py").write_text("b\n")

    runtime = _make_runtime()
    events = await _collect(runtime, worktree_path=str(repo), starting_seq=42)

    seqs = [ev.seq for ev in events]
    # Strictly increasing, starts at 42 (regardless of order between A/B).
    assert seqs == sorted(seqs)
    assert seqs[0] == 42
    assert seqs[-1] == 42 + len(events) - 1


@pytest.mark.asyncio
async def test_scan_file_changes_no_worktree_yields_nothing() -> None:
    runtime = _make_runtime()
    events = await _collect(runtime, worktree_path=None)
    assert events == []

    events_empty = await _collect(runtime, worktree_path="")
    assert events_empty == []


@pytest.mark.asyncio
async def test_scan_file_changes_non_git_dir_yields_nothing(tmp_path: Path) -> None:
    """Non-git directory should not raise — just yield nothing."""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "f.txt").write_text("x")

    runtime = _make_runtime()
    events = await _collect(runtime, worktree_path=str(plain))
    assert events == []


@pytest.mark.asyncio
async def test_scan_file_changes_clean_repo_yields_nothing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    await _init_repo(repo)

    runtime = _make_runtime()
    events = await _collect(runtime, worktree_path=str(repo))
    assert events == []
