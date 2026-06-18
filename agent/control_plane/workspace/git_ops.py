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


async def get_name_status(
    worktree_path: Path,
    *,
    base: str = "HEAD",
) -> list[tuple[str, str]]:
    """Return ``[(status, path), ...]`` for each changed file in *worktree_path* vs *base*.

    Status codes (single-letter form):
      - ``"A"`` — added (create)
      - ``"M"`` — modified (edit)
      - ``"D"`` — deleted
      - ``"R"`` — renamed (we surface as edit with the new path)
      - ``"C"`` — copied (we surface as create)
      - ``"T"`` — type change (we surface as edit)

    Workflow:
      1. ``git diff --name-status base`` — picks up tracked changes (modify/delete/
         add-with-staging) since *base*.
      2. ``git ls-files --others --exclude-standard`` — picks up brand-new
         untracked files that haven't been ``git add``-ed yet (Claude often
         leaves files untracked because it has no reason to stage them).

    Both lists are merged with the tracked status taking precedence (a file
    showing up in (1) is never reported again from (2)).

    Resilient to empty repos: when ``git diff base`` fails (e.g. no HEAD), falls
    back to the staged index (``--cached``).
    """
    rc, out, _err = await _run_git(
        "diff", "--name-status", base,
        cwd=worktree_path,
    )
    if rc != 0:
        rc, out, _err = await _run_git(
            "diff", "--name-status", "--cached",
            cwd=worktree_path,
        )
        if rc != 0:
            out = ""

    seen: set[str] = set()
    entries: list[tuple[str, str]] = []
    for line in out.splitlines():
        line = line.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status_field = parts[0].strip()
        # Renames look like 'R100\told\tnew' — collapse to single letter + new path.
        status = status_field[0] if status_field else "M"
        if status in ("R", "C") and len(parts) >= 3:
            path = parts[-1]
        else:
            path = parts[1]
        if path in seen:
            continue
        seen.add(path)
        entries.append((status, path))

    # Untracked files (added but not staged).
    rc, out2, _err2 = await _run_git(
        "ls-files", "--others", "--exclude-standard",
        cwd=worktree_path,
    )
    if rc == 0:
        for line in out2.splitlines():
            path = line.strip()
            if not path or path in seen:
                continue
            seen.add(path)
            entries.append(("A", path))

    return entries


async def get_unified_diff(
    worktree_path: Path,
    *,
    base: str = "HEAD",
    paths: list[str] | None = None,
    context_lines: int = 3,
) -> dict[str, str]:
    """Return per-file unified diff text for changes in *worktree_path* vs *base*.

    Result map: ``{path: diff_text}`` where ``diff_text`` is the standard
    unified diff (including the ``diff --git`` header) for that single file.

    Args:
        worktree_path: Worktree root.
        base: Diff base ref (default ``HEAD``).
        paths: Optional explicit path list. When omitted, all changed files
            from :func:`get_name_status` are included (tracked + untracked).
        context_lines: ``-U<N>`` value for git diff. Default 3.

    Workflow:
      1. Resolve the path list:
         - If ``paths`` given, use it verbatim.
         - Otherwise call :func:`get_name_status` to discover changes.
      2. For each path, decide tracked vs untracked:
         - Tracked → ``git diff -U<N> base -- <path>``
         - Untracked → ``git diff --no-index -U<N> /dev/null <path>``
           (returns rc=1 on diff present, which is normal)
      3. Empty diff (no actual textual change) → skip; don't include the key.

    Resilient to:
      - empty repo (base resolution falls back to ``--cached``)
      - binary files (diff body simply says "Binary files differ"; we keep it)
      - non-existent paths (silently skipped, no entry in the result)
    """
    if paths is None:
        entries = await get_name_status(worktree_path, base=base)
        paths = [p for _status, p in entries]

    if not paths:
        return {}

    # Discover which files are tracked vs untracked, so we know which
    # form of `git diff` to use.
    rc, tracked_out, _ = await _run_git(
        "ls-files", "--", *paths,
        cwd=worktree_path,
    )
    tracked_set: set[str] = set()
    if rc == 0:
        for line in tracked_out.splitlines():
            line = line.strip()
            if line:
                tracked_set.add(line)

    result: dict[str, str] = {}
    ctx_flag = f"-U{max(0, int(context_lines))}"

    for path in paths:
        if path in tracked_set:
            rc, out, _err = await _run_git(
                "diff", ctx_flag, base, "--", path,
                cwd=worktree_path,
            )
            if rc != 0:
                # Empty repo / unknown base — fall back to staged index.
                rc, out, _err = await _run_git(
                    "diff", ctx_flag, "--cached", "--", path,
                    cwd=worktree_path,
                )
                if rc != 0:
                    continue
            if out.strip():
                result[path] = out
        else:
            # Untracked: synthesize diff against /dev/null. rc=1 is "diff present".
            rc, out, _err = await _run_git(
                "diff", "--no-index", ctx_flag, "/dev/null", path,
                cwd=worktree_path,
            )
            # rc 0 = no diff (rare for an untracked file); rc 1 = diff present;
            # rc >1 = real failure.
            if rc <= 1 and out.strip():
                result[path] = out

    return result


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
