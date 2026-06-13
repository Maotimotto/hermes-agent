"""Git worktree operations: async wrappers around git CLI.

All functions use asyncio.create_subprocess_exec (no shell=True).
Paths are handled via pathlib.Path throughout.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .types import DiffEntry


async def _run_git(*args: str, cwd: str | Path | None = None) -> tuple[int, str, str]:
    """Run a git command and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    return proc.returncode, stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace")


async def validate_repo(repo_path: Path) -> None:
    """Check that *repo_path* is a valid git repository.

    Raises:
        ValueError: if not a git repo or base_branch doesn't exist.
    """
    rc, _, err = await _run_git("rev-parse", "--git-dir", cwd=repo_path)
    if rc != 0:
        raise ValueError(f"Not a git repository: {repo_path} — {err.strip()}")


async def validate_branch(repo_path: Path, branch: str) -> None:
    """Check that *branch* exists in the repo.

    Raises:
        ValueError: if the branch does not exist.
    """
    rc, out, _ = await _run_git("rev-parse", "--verify", branch, cwd=repo_path)
    if rc != 0:
        raise ValueError(f"Branch does not exist: {branch} in {repo_path}")


async def create_worktree(
    repo_path: Path,
    base_branch: str,
    target_path: Path,
    branch_name: str,
) -> None:
    """Create a new git worktree.

    Args:
        repo_path: Root of the main git repo.
        base_branch: Existing branch/commit to fork from.
        target_path: Directory where the worktree will be checked out.
        branch_name: Name of the new branch for this worktree.

    Raises:
        RuntimeError: if the git command fails.
    """
    # Ensure parent dir exists
    target_path.parent.mkdir(parents=True, exist_ok=True)

    rc, out, err = await _run_git(
        "worktree", "add",
        "-b", branch_name,
        str(target_path),
        base_branch,
        cwd=repo_path,
    )
    if rc != 0:
        raise RuntimeError(
            f"Failed to create worktree at {target_path}: {err.strip()}"
        )


async def remove_worktree(worktree_path: Path, *, force: bool = False) -> None:
    """Remove a git worktree.

    Args:
        worktree_path: Path of the worktree to remove.
        force: If True, pass --force to git worktree remove.

    Raises:
        RuntimeError: if the git command fails (and force is False).
    """
    # The worktree may live inside another repo, so we need to find the
    # main repo to issue `git worktree remove`.  git stores the common
    # dir in `.git` (which is a file for worktrees).
    git_file = worktree_path / ".git"
    if not git_file.exists():
        raise RuntimeError(f"Not a valid worktree: {worktree_path}")

    cmd = ["worktree", "remove"]
    if force:
        cmd.append("--force")
    cmd.append(str(worktree_path))

    # We need to run from the main repo.  Read the gitdir link.
    common_dir = _resolve_worktree_common_dir(worktree_path)
    rc, out, err = await _run_git(*cmd, cwd=common_dir)
    if rc != 0:
        raise RuntimeError(
            f"Failed to remove worktree {worktree_path}: {err.strip()}"
        )


async def get_diff(worktree_path: Path) -> list[DiffEntry]:
    """Get a diff summary of changes in *worktree_path* vs its HEAD.

    Returns a list of DiffEntry with per-file additions/deletions.
    Uses `git diff --numstat` for efficient per-file stat counting.
    """
    rc, out, err = await _run_git(
        "diff", "--numstat", "HEAD",
        cwd=worktree_path,
    )
    if rc != 0:
        # If there's no HEAD (e.g. empty repo), fall back to diff-index
        rc, out, err = await _run_git(
            "diff", "--numstat", "--cached",
            cwd=worktree_path,
        )
        if rc != 0:
            return []

    entries: list[DiffEntry] = []
    for line in out.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_str, del_str, filepath = parts[0], parts[1], parts[2]
        # Binary files show '-' for both
        additions = int(add_str) if add_str != "-" else 0
        deletions = int(del_str) if del_str != "-" else 0
        entries.append(DiffEntry(path=filepath, additions=additions, deletions=deletions))
    return entries


async def is_clean(worktree_path: Path) -> bool:
    """Check whether *worktree_path* has no uncommitted changes.

    Returns True if the worktree is clean (no staged, unstaged, or
    untracked changes).
    """
    # --porcelain gives machine-readable output; empty = clean
    rc, out, _ = await _run_git(
        "status", "--porcelain",
        cwd=worktree_path,
    )
    return rc == 0 and out.strip() == ""


# ── helpers ───────────────────────────────────────────────────────────


def _resolve_worktree_common_dir(worktree_path: Path) -> Path:
    """Resolve the main repo path from a worktree's .git file.

    A worktree's .git file contains a line like:
        gitdir: /home/user/project/.git/worktrees/<name>
    We need the main repo root so we can run `git worktree remove`.
    """
    git_file = worktree_path / ".git"
    if git_file.is_file():
        content = git_file.read_text().strip()
        for line in content.splitlines():
            if line.startswith("gitdir:"):
                gitdir = Path(line[len("gitdir:"):].strip())
                # .git/worktrees/<name>/../../.. = .git → repo root
                common_dir = gitdir.parent.parent.parent.resolve()
                return common_dir
    # Fallback: assume worktree_path itself is the repo
    return worktree_path
