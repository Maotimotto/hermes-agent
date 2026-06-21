"""Session log export — V1.1 P1 基础日志导出.

把一个 session 的全部上下文（元数据 + 事件流 + 审批记录 + 文件变更聚合）
打包成 JSON 或 Markdown 供用户下载。

Endpoints (under /control-plane prefix):
  GET /sessions/{session_id}/export.json — 完整 JSON 快照
  GET /sessions/{session_id}/export.md   — 同一份数据的 Markdown 渲染

设计取舍：
  - 一次性拉满（limit=10000）：导出场景，分页只会让客户端更难拼，事件量超
    1w 的 session 是异常用例可后续优化。
  - file.changed 按 path 聚合保留最后一条（同一 path 多次写只展示最终状态）。
  - Markdown payload 截到 2KB / diff 截到 4KB：避免长 diff 把 .md 文件撑爆。
  - 三反引号 fence 防御：把 payload 里的 ``` 换成 ``ʼ`` 避免破 fence。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from agent.control_plane.store import SessionStore
from gateway.control_plane.deps import get_store

router = APIRouter(prefix="/sessions", tags=["exports"])

EVENTS_EXPORT_LIMIT = 10000  # 单 session 一次导出最多 10000 事件
MD_PAYLOAD_TRUNC = 2000
MD_DIFF_TRUNC = 4000


def _safe_fence(text: str) -> str:
    """避免 payload 内含三反引号撑爆 markdown fence。"""
    return text.replace("```", "``ʼ``")


async def _gather(session_id: str, store: SessionStore) -> dict[str, Any]:
    sess = await store.get_session(session_id)
    if sess is None:
        raise HTTPException(
            status_code=404,
            detail=f"session not found: {session_id}",
        )
    events = await store.list_events(session_id, limit=EVENTS_EXPORT_LIMIT)
    approvals = await store.list_approvals_by_session(session_id)

    # file.changed 聚合：同 path 取最新一条
    file_changes: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.type == "file.changed":
            payload = e.payload or {}
            path = payload.get("path")
            if path:
                file_changes[path] = payload

    return {
        "session": {
            "id": sess.id,
            "runtime_kind": sess.runtime_kind,
            "model": sess.model,
            "repo_path": sess.repo_path,
            "workspace_id": sess.workspace_id,
            "status": sess.status,
            "started_at": sess.started_at,
            "ended_at": sess.ended_at,
            "metadata": sess.metadata,
        },
        "events": [
            {
                "id": e.id,
                "session_id": e.session_id,
                "turn_id": e.turn_id,
                "type": e.type,
                "payload": e.payload,
                "created_at": e.created_at,
            }
            for e in events
        ],
        "approvals": [
            {
                "id": a.id,
                "session_id": a.session_id,
                "turn_id": a.turn_id,
                "action_kind": a.action_kind,
                "action_payload": a.action_payload,
                "risk": a.risk,
                "decision": a.decision,
                "decided_at": a.decided_at,
                "decided_by": a.decided_by,
                "ttl_until": a.ttl_until,
            }
            for a in approvals
        ],
        "file_changes": [
            {"path": p, **{k: v for k, v in payload.items() if k != "path"}}
            for p, payload in sorted(file_changes.items())
        ],
        "stats": {
            "event_count": len(events),
            "approval_count": len(approvals),
            "file_change_count": len(file_changes),
        },
    }


@router.get("/{session_id}/export.json")
async def export_json(
    session_id: str,
    store: SessionStore = Depends(get_store),
) -> Response:
    data = await _gather(session_id, store)
    return Response(
        content=json.dumps(data, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="session-{session_id}.json"',
        },
    )


@router.get("/{session_id}/export.md")
async def export_md(
    session_id: str,
    store: SessionStore = Depends(get_store),
) -> Response:
    data = await _gather(session_id, store)
    md = _render_markdown(data)
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="session-{session_id}.md"',
        },
    )


def _render_markdown(data: dict[str, Any]) -> str:
    s = data["session"]
    stats = data["stats"]
    lines: list[str] = [
        f"# Session {s['id']}",
        "",
        f"_{stats['event_count']} events · {stats['approval_count']} approvals · "
        f"{stats['file_change_count']} file changes_",
        "",
        "## Metadata",
        "",
    ]
    for k in (
        "runtime_kind",
        "model",
        "repo_path",
        "workspace_id",
        "status",
        "started_at",
        "ended_at",
    ):
        v = s.get(k)
        lines.append(f"- **{k}**: {v if v is not None else '-'}")
    if s.get("metadata"):
        lines += [
            "",
            "```json",
            _safe_fence(json.dumps(s["metadata"], indent=2, ensure_ascii=False)),
            "```",
        ]

    lines += ["", "## Timeline", ""]
    if not data["events"]:
        lines.append("_No events._")
    for e in data["events"]:
        ts = e.get("created_at") or "-"
        turn = e.get("turn_id") or "-"
        lines.append(f"### `{ts}` **{e['type']}** (turn={turn})")
        if e.get("payload"):
            payload_text = json.dumps(e["payload"], indent=2, ensure_ascii=False)
            truncated = False
            if len(payload_text) > MD_PAYLOAD_TRUNC:
                payload_text = payload_text[:MD_PAYLOAD_TRUNC]
                truncated = True
            lines += ["```json", _safe_fence(payload_text), "```"]
            if truncated:
                lines.append("_…payload truncated._")
        lines.append("")

    lines += ["## Approvals", ""]
    if not data["approvals"]:
        lines.append("_No approvals._")
    for a in data["approvals"]:
        lines.append(
            f"- `{a['id']}` **{a['action_kind']}** "
            f"risk={a.get('risk') or '-'} → "
            f"{a.get('decision') or 'pending'} "
            f"by {a.get('decided_by') or '-'} at {a.get('decided_at') or '-'}"
        )

    lines += ["", "## File Changes", ""]
    if not data["file_changes"]:
        lines.append("_No file changes._")
    for fc in data["file_changes"]:
        op = (fc.get("operation") or "edit").upper()
        lines.append(f"### {op} `{fc['path']}`")
        diff = fc.get("diff")
        if diff:
            truncated = False
            if len(diff) > MD_DIFF_TRUNC:
                diff = diff[:MD_DIFF_TRUNC]
                truncated = True
            lines += ["```diff", _safe_fence(diff), "```"]
            if truncated:
                lines.append("_…diff truncated._")
        lines.append("")

    return "\n".join(lines) + "\n"
