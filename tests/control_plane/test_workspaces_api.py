"""HTTP integration tests for /control-plane/workspaces/* routes.

We build a real git repo + real WorkspaceManager + TestClient, no mocks.
This exercises the same code path the daemon would hit in production.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.control_plane.workspace import WorkspaceManager
from gateway.control_plane.app import create_control_plane_app, init_store
from gateway.control_plane.deps import AppState


# ── helpers ───────────────────────────────────────────────────────────


async def _run_git(*args: str, cwd: Path) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode(), err.decode()


async def _init_repo(repo_path: Path) -> None:
    """Init a git repo with a single commit on 'main'."""
    repo_path.mkdir(parents=True, exist_ok=True)
    await _run_git("init", "-b", "main", cwd=repo_path)
    await _run_git("config", "user.email", "test@hermes.local", cwd=repo_path)
    await _run_git("config", "user.name", "Hermes Test", cwd=repo_path)
    (repo_path / "README.md").write_text("initial line\n")
    await _run_git("add", ".", cwd=repo_path)
    await _run_git("commit", "-m", "init", cwd=repo_path)


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def app_with_workspace(tmp_path: Path) -> tuple[FastAPI, FastAPI, str]:
    """Build a control-plane app with a real workspace and return (main, cp, ws_id)."""
    repo = tmp_path / "repo"
    ws_root = tmp_path / "workspaces"
    db_path = tmp_path / "cp.db"

    cp = create_control_plane_app(db_path=str(db_path), run_migrations=True)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(init_store(cp))

        # Replace the auto-built workspace manager with one rooted in tmp_path.
        state: AppState = cp.state.cp  # type: ignore[assignment]
        state.workspace_manager = WorkspaceManager(workspaces_root=ws_root)

        # Init repo + create workspace + make some changes inside the worktree.
        loop.run_until_complete(_init_repo(repo))
        ws = loop.run_until_complete(
            state.workspace_manager.create_workspace(
                repo_path=repo,
                base_branch="main",
                session_id="sess_test",
            )
        )
        # Make 3 changes so the diff has something interesting:
        # - modify README.md
        # - add a brand-new untracked file
        # - delete... nothing yet (we test deletion in a separate fixture)
        wt = Path(ws.worktree_path)
        (wt / "README.md").write_text("initial line\nadded line\n")
        (wt / "added.py").write_text("print('hello')\n")
    finally:
        loop.close()

    main_app = FastAPI()
    main_app.mount("/control-plane", cp)
    return main_app, cp, ws.id


@pytest.fixture()
def client(app_with_workspace: tuple[FastAPI, FastAPI, str]) -> Iterator[TestClient]:
    main_app, _cp, _ws_id = app_with_workspace
    with TestClient(main_app, raise_server_exceptions=False) as c:
        yield c


# ── /workspaces/{id}/diff (summary) ───────────────────────────────────


def test_diff_summary_returns_changed_files(
    client: TestClient,
    app_with_workspace: tuple[FastAPI, FastAPI, str],
) -> None:
    """Summary endpoint should list the changed files with status + counts."""
    _, _, ws_id = app_with_workspace
    resp = client.get(f"/control-plane/workspaces/{ws_id}/diff")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["workspace_id"] == ws_id
    assert body["total_files"] >= 2

    by_path = {f["path"]: f for f in body["files"]}
    assert "README.md" in by_path
    assert by_path["README.md"]["status"] == "edit"
    assert by_path["README.md"]["additions"] >= 1

    assert "added.py" in by_path
    assert by_path["added.py"]["status"] == "create"
    # Untracked files don't show up in numstat → additions = 0 in summary
    # (the unified-diff endpoint will still show their content).
    assert by_path["added.py"]["deletions"] == 0


def test_diff_summary_unknown_workspace_returns_404(client: TestClient) -> None:
    resp = client.get("/control-plane/workspaces/does-not-exist/diff")
    assert resp.status_code == 404
    body = resp.json()
    # Error envelope from install_error_handlers: {"error": {...}}
    assert "error" in body or "detail" in body


# ── /workspaces/{id}/diff/unified ─────────────────────────────────────


def test_unified_diff_returns_diff_text_per_file(
    client: TestClient,
    app_with_workspace: tuple[FastAPI, FastAPI, str],
) -> None:
    _, _, ws_id = app_with_workspace
    resp = client.get(f"/control-plane/workspaces/{ws_id}/diff/unified")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["workspace_id"] == ws_id

    diffs = body["diffs"]
    assert "README.md" in diffs
    assert "+added line" in diffs["README.md"]

    assert "added.py" in diffs
    assert "+print('hello')" in diffs["added.py"]


def test_unified_diff_filter_by_paths(
    client: TestClient,
    app_with_workspace: tuple[FastAPI, FastAPI, str],
) -> None:
    """`?paths=foo&paths=bar` should restrict the response."""
    _, _, ws_id = app_with_workspace
    resp = client.get(
        f"/control-plane/workspaces/{ws_id}/diff/unified",
        params=[("paths", "README.md")],
    )
    assert resp.status_code == 200, resp.text
    diffs = resp.json()["diffs"]
    assert list(diffs.keys()) == ["README.md"]


def test_unified_diff_context_lines_param(
    client: TestClient,
    app_with_workspace: tuple[FastAPI, FastAPI, str],
) -> None:
    """``?context_lines=0`` reduces context — sanity check the param is wired."""
    _, _, ws_id = app_with_workspace
    resp = client.get(
        f"/control-plane/workspaces/{ws_id}/diff/unified",
        params={"context_lines": 0},
    )
    assert resp.status_code == 200, resp.text


def test_unified_diff_unknown_workspace_returns_404(client: TestClient) -> None:
    resp = client.get("/control-plane/workspaces/does-not-exist/diff/unified")
    assert resp.status_code == 404


def test_unified_diff_context_lines_out_of_range_rejected(
    client: TestClient,
    app_with_workspace: tuple[FastAPI, FastAPI, str],
) -> None:
    """``context_lines>20`` is rejected by Query(..., le=20)."""
    _, _, ws_id = app_with_workspace
    resp = client.get(
        f"/control-plane/workspaces/{ws_id}/diff/unified",
        params={"context_lines": 999},
    )
    assert resp.status_code == 422
