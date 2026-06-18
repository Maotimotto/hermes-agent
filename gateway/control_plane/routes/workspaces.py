"""Workspace inspection routes — diff 面板用。

Endpoints:
  GET /workspaces/{id}/diff           — 摘要：每个文件的状态 + 增删行数
  GET /workspaces/{id}/diff/unified   — 完整 unified diff 文本

Workspace 创建走 ``POST /sessions``（body 带 ``repo_path``），这里只读。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from agent.control_plane.workspace import WorkspaceManager
from gateway.control_plane.deps import get_workspace_manager
from gateway.control_plane.schemas import (
    WorkspaceDiffSummaryResponse,
    WorkspaceFileEntry,
    WorkspaceUnifiedDiffResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


# ── helpers ────────────────────────────────────────────────────────────


# git --name-status 字母 → file.changed 事件用的状态字串
_STATUS_MAP = {
    "A": "create",
    "C": "create",
    "?": "create",  # 来自 ls-files --others 兜底（理论上 get_name_status 已映射成 A）
    "D": "delete",
    "M": "edit",
    "R": "edit",
    "T": "edit",
}


def _normalize_status(letter: str) -> str:
    return _STATUS_MAP.get(letter, "edit")


# ── GET /workspaces/{id}/diff ─────────────────────────────────────────


@router.get("/{workspace_id}/diff", response_model=WorkspaceDiffSummaryResponse)
async def get_workspace_diff_summary(
    workspace_id: str,
    base: str = Query("HEAD", description="Diff 基准 ref，默认 HEAD"),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
) -> WorkspaceDiffSummaryResponse:
    """返回 workspace 内每个变更文件的状态 + 增删行数。

    实现：
      1. ``manager.get_name_status`` 拿到 ``[(letter, path), ...]``；
         决定每个文件的语义状态（create/edit/delete）。
      2. ``manager.get_diff`` 拿 ``DiffEntry`` 列表（含 additions/deletions），
         按 path 合并到上面的状态信息上。
      3. 汇总成 ``WorkspaceDiffSummaryResponse``。

    deleted 文件 ``additions`` 在 numstat 里通常 = 0，``deletions`` = 原行数。
    新增的二进制文件 numstat 显示 "-\\t-"（在 git_ops 里已转成 0/0）。
    """
    try:
        name_status = await workspace_manager.get_name_status(workspace_id, base=base)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        diff_entries = await workspace_manager.get_diff(workspace_id)
    except KeyError as exc:
        # 理论上 name_status 已 raise 过，这里兜底
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # numstat 不会列出未跟踪文件 → 用 path 取并集
    stats_by_path = {d.path: d for d in diff_entries}

    files: list[WorkspaceFileEntry] = []
    total_add = 0
    total_del = 0

    for letter, path in name_status:
        stat = stats_by_path.get(path)
        additions = stat.additions if stat is not None else 0
        deletions = stat.deletions if stat is not None else 0
        files.append(
            WorkspaceFileEntry(
                path=path,
                status=_normalize_status(letter),
                additions=additions,
                deletions=deletions,
            )
        )
        total_add += additions
        total_del += deletions

    return WorkspaceDiffSummaryResponse(
        workspace_id=workspace_id,
        files=files,
        total_additions=total_add,
        total_deletions=total_del,
        total_files=len(files),
    )


# ── GET /workspaces/{id}/diff/unified ─────────────────────────────────


@router.get(
    "/{workspace_id}/diff/unified",
    response_model=WorkspaceUnifiedDiffResponse,
)
async def get_workspace_diff_unified(
    workspace_id: str,
    base: str = Query("HEAD", description="Diff 基准 ref，默认 HEAD"),
    paths: Optional[list[str]] = Query(
        None,
        description=(
            "可选路径过滤；可重复传 ``?paths=a.py&paths=b.py``。"
            "未传时返回所有变更文件。"
        ),
    ),
    context_lines: int = Query(
        3, ge=0, le=20, description="``-U<N>`` 上下文行数，默认 3"
    ),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
) -> WorkspaceUnifiedDiffResponse:
    """返回 ``{path: unified_diff_text}``。

    与摘要接口分开是因为 unified diff 体积可能很大（一个大文件几 MB），
    前端按需取（点开某个文件再请求那个 path）。
    """
    try:
        diffs = await workspace_manager.get_unified_diff(
            workspace_id,
            base=base,
            paths=paths,
            context_lines=context_lines,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return WorkspaceUnifiedDiffResponse(
        workspace_id=workspace_id,
        diffs=diffs,
    )
