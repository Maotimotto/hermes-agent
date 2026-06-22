"""Provider Handoff routes — V1.1 P1 『Claude ↔ Codex 双向手动转交』.

实现两种手动转交（合并一个端点 + ``kind`` 区分）：

- ``transfer``：当前 session 是 Claude → 把最近 N 条 assistant.message + 可选的
  workspace diff 拼成 initial_prompt，调用目标 provider（codex）的 runtime
  起一个新 session，让 Codex 接着干。
- ``review``：当前 session 是 Codex → 把当前 workspace 的 unified diff 打包
  成 initial_prompt，让 Claude 起一个新 session 做 review。

端点：

- ``POST /control-plane/sessions/{from_session_id}/handoff``
    body = HandoffCreate；返回新 session id + handoff_id。
- ``GET  /control-plane/sessions/{session_id}/handoffs``
    列出该 session 出向 + 入向的 handoff 记录。
- ``GET  /control-plane/handoffs/{handoff_id}``
    单个 handoff 详情。

持久化方式：内存（AppState.handoffs + AppState.handoffs_by_session）。重启
清空可接受 — handoff 是触发新 session 的一次性操作，新 session 本身已经
持久化。详见 deps.py 的注释。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from agent.control_plane.ids import new_handoff_id, new_session_id
from agent.control_plane.runtimes.interface import StartSessionInput
from agent.control_plane.store import SessionRecord, SessionStore
from gateway.control_plane.deps import (
    AppState,
    get_app_state,
    get_store,
)
from gateway.control_plane.schemas import (
    HandoffCreate,
    HandoffListResponse,
    HandoffRecord,
    HandoffResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["handoffs"])

# 最长 prompt 字节数（避免把 100MB 的 binary diff 灌给 LLM）
_MAX_PROMPT_BYTES = 64 * 1024
# 单文件 diff 最长字节数（多文件按比例分配）
_MAX_DIFF_PER_FILE = 8 * 1024


# ── helpers ─────────────────────────────────────────────────────────────────


async def _collect_recent_messages(
    store: SessionStore, session_id: str, *, limit: int
) -> list[str]:
    """拉最近 limit 条 assistant.message / assistant.delta 的文本。

    优先用 assistant.message（完整消息）；若一条 message 都没有，
    退化用 assistant.delta（流式片段）拼起来。

    实现说明：store.list_events 按 id ASC 排序，所以拉一个大窗口再取尾部。
    handoff 场景上下文最多几十条 message 已经够，window=500 兜底到位。
    """
    if limit <= 0:
        return []
    WINDOW = 500
    events = await store.list_events(
        session_id,
        types=["assistant.message"],
        limit=WINDOW,
    )
    if not events:
        # fallback：拼最近的 delta
        events = await store.list_events(
            session_id,
            types=["assistant.delta"],
            limit=WINDOW * 4,  # delta 一般比 message 多
        )
    # 取尾部 limit 条 = "最近 N 条"
    events = events[-limit:]
    texts: list[str] = []
    for e in events:
        text = e.payload.get("text") if isinstance(e.payload, dict) else None
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
    return texts


async def _collect_diff_text(
    state: AppState, workspace_id: str | None
) -> str:
    """拼 workspace 的 unified diff（含简单截断保护）。"""
    if not workspace_id:
        return ""
    try:
        diffs = await state.workspace_manager.get_unified_diff(workspace_id)
    except KeyError:
        logger.warning(
            "[handoff] workspace not found, skip diff: %s", workspace_id
        )
        return ""
    except Exception:
        logger.exception("[handoff] get_unified_diff failed")
        return ""

    parts: list[str] = []
    for path, diff in diffs.items():
        if not diff:
            continue
        truncated = diff
        if len(diff) > _MAX_DIFF_PER_FILE:
            truncated = (
                diff[:_MAX_DIFF_PER_FILE]
                + f"\n... [truncated; original {len(diff)} bytes]"
            )
        parts.append(f"### diff: {path}\n```diff\n{truncated}\n```")
    return "\n\n".join(parts)


def _build_initial_prompt(
    *,
    kind: str,
    from_provider: str,
    to_provider: str,
    messages: list[str],
    diff_text: str,
    extra_prompt: str | None,
) -> tuple[str, str]:
    """拼最终给目标 provider 的 initial_prompt 以及一段供 UI 展示的 context_summary。

    返回 (initial_prompt, context_summary)。
    """
    header = (
        "# Hermes Provider Handoff\n\n"
        f"You are receiving a handoff from `{from_provider}` to `{to_provider}`.\n"
        f"Handoff kind: **{kind}**.\n"
    )
    if kind == "review":
        instruction = (
            "Your job is to **review** the diff produced by the previous "
            "session. Read the diff carefully and reply with concrete review "
            "comments (issues, risks, suggestions). Do not modify files unless "
            "explicitly asked."
        )
    else:
        instruction = (
            "Continue the work started in the previous session. Use the "
            "context summary and the diff (if any) as your starting point."
        )

    sections: list[str] = [header, instruction]

    if messages:
        joined = "\n\n---\n\n".join(messages[-len(messages):])
        sections.append("## Previous assistant messages\n\n" + joined)

    if diff_text:
        sections.append("## Current workspace diff\n\n" + diff_text)

    if extra_prompt:
        sections.append("## Additional instructions from user\n\n" + extra_prompt)

    prompt = "\n\n".join(sections)
    if len(prompt) > _MAX_PROMPT_BYTES:
        prompt = (
            prompt[:_MAX_PROMPT_BYTES]
            + f"\n... [truncated; original {len(prompt)} bytes]"
        )

    # context_summary 给前端列表用：第一条 message 的前 200 字
    if messages:
        first = messages[-1]
        context_summary = first[:200] + ("…" if len(first) > 200 else "")
    elif diff_text:
        context_summary = f"diff ({len(diff_text)} bytes)"
    else:
        context_summary = extra_prompt[:200] if extra_prompt else ""

    return prompt, context_summary


def _record_handoff(state: AppState, record: dict[str, Any]) -> None:
    """把 handoff 记录写入 AppState 的两个索引。"""
    state.handoffs[record["id"]] = record
    state.handoffs_by_session.setdefault(record["from_session_id"], []).append(
        record["id"]
    )
    to_sid = record.get("to_session_id")
    if to_sid:
        state.handoffs_by_session.setdefault(to_sid, []).append(record["id"])


# ── POST /sessions/{from_session_id}/handoff ────────────────────────────────


@router.post(
    "/sessions/{from_session_id}/handoff",
    response_model=HandoffResponse,
    status_code=201,
)
async def create_handoff(
    from_session_id: str,
    body: HandoffCreate,
    store: SessionStore = Depends(get_store),
    state: AppState = Depends(get_app_state),
) -> HandoffResponse:
    """把当前 session 转交给另一个 provider。

    流程：
      1. 校验来源 session 存在。
      2. 校验目标 provider runtime 已注册且健康（503 兜底）。
      3. 拉最近 N 条 assistant.message + （可选）workspace diff，拼 prompt。
      4. 新 session_id 入库 → 调 target_runtime.start_session。
      5. 把 handoff 记录写入内存索引（含出/入两条快引用）。
      6. 把『初始 prompt』作为第一条事件写入新 session（让前端立刻能看到）。
    """
    src = await store.get_session(from_session_id)
    if src is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session not found: {from_session_id}",
        )

    from_provider = (
        state.runtime_registry.get_session_runtime(from_session_id)
        or src.runtime_kind
    )
    to_provider = body.target_provider

    target_runtime = state.runtime_registry.get(to_provider)
    if target_runtime is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "provider_unavailable",
                "kind": to_provider,
                "message": (
                    f"Target provider '{to_provider}' is not registered."
                ),
            },
        )

    # 健康门禁 — 与 turns 路由一致
    monitor = state.provider_health
    if monitor is not None:
        snap = monitor.snapshot(to_provider)
        if snap is not None and not snap.available and snap.error is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "provider_unavailable",
                    "kind": to_provider,
                    "message": (
                        snap.message
                        or f"Provider {to_provider} is currently unavailable."
                    ),
                },
            )

    # 收集上下文
    messages = await _collect_recent_messages(
        store, from_session_id, limit=body.context_messages
    )
    diff_text = ""
    if body.include_diff:
        diff_text = await _collect_diff_text(state, src.workspace_id)

    initial_prompt, context_summary = _build_initial_prompt(
        kind=body.kind,
        from_provider=from_provider,
        to_provider=to_provider,
        messages=messages,
        diff_text=diff_text,
        extra_prompt=body.extra_prompt,
    )

    # 启目标 session — 复用 sessions.py 的核心逻辑（简化版）
    new_sid = new_session_id()
    new_record = SessionRecord(
        id=new_sid,
        runtime_kind=to_provider,
        model=src.model,
        repo_path=src.repo_path,
        # 复用同一个 workspace 而不是建新 worktree —— review 场景必须能看到
        # 上一个 session 改过的文件；transfer 场景共享 workspace 也合理（同一
        # 任务的延续）。如果将来需要隔离，可以加一个 ``isolate_workspace`` 参数。
        workspace_id=src.workspace_id,
        status="created",
        metadata={
            "handoff_from": from_session_id,
            "handoff_kind": body.kind,
            "handoff_from_provider": from_provider,
        },
    )
    await store.create_session(new_record)

    handoff_id = new_handoff_id()
    handoff_status = "ok"
    handoff_error: str | None = None

    try:
        if src.repo_path:
            ref = await target_runtime.start_session(
                StartSessionInput(
                    repo_path=src.repo_path,
                    branch="main",  # 沿用 base_branch 概念；用 sessions 默认值
                    hermes_session_id=new_sid,
                )
            )
            state.runtime_registry.bind_session(new_sid, to_provider)
            if ref.provider_session_id:
                merged = dict(new_record.metadata or {})
                merged["provider_session_id"] = ref.provider_session_id
                new_record.metadata = merged
            await store.update_session_status(new_sid, "running")
            new_record.status = "running"
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[handoff] target runtime start_session failed kind=%s sid=%s",
            to_provider,
            new_sid[:8],
        )
        handoff_status = "failed"
        handoff_error = f"{type(exc).__name__}: {exc}"
        # 不抛 500 — handoff 记录仍然产生，新 session 留在 'created' 状态，
        # 让用户在 UI 上看到失败再决定下一步。

    # 把 initial prompt 作为一条事件落到新 session（便于前端在新 session
    # 详情页直接看到 handoff 内容；type 用 ``handoff.received`` 自定义事件）。
    state.event_bus.publish(
        new_sid,
        {
            "type": "handoff.received",
            "session_id": new_sid,
            "handoff_id": handoff_id,
            "from_session_id": from_session_id,
            "from_provider": from_provider,
            "kind": body.kind,
            "prompt": initial_prompt,
        },
    )

    # 持久化 handoff 记录到内存索引
    record = {
        "id": handoff_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "from_session_id": from_session_id,
        "to_session_id": new_sid,
        "from_provider": from_provider,
        "to_provider": to_provider,
        "kind": body.kind,
        "context_summary": context_summary,
        "initial_prompt": initial_prompt,
        "status": handoff_status,
        "error": handoff_error,
    }
    _record_handoff(state, record)

    return HandoffResponse(
        handoff_id=handoff_id,
        to_session_id=new_sid,
        from_provider=from_provider,
        to_provider=to_provider,
        kind=body.kind,
        status=handoff_status,
    )


# ── GET /sessions/{session_id}/handoffs ─────────────────────────────────────


@router.get(
    "/sessions/{session_id}/handoffs",
    response_model=HandoffListResponse,
)
async def list_handoffs(
    session_id: str,
    store: SessionStore = Depends(get_store),
    state: AppState = Depends(get_app_state),
) -> HandoffListResponse:
    """列出某 session 关联的 handoff 记录（出向 + 入向）。

    若 session 不存在返回 404；存在但没有 handoff 返回空数组。
    """
    if await store.get_session(session_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session not found: {session_id}",
        )

    ids = state.handoffs_by_session.get(session_id, [])
    # 去重（一条 handoff 在 from/to 两个 session 上都会被引用一次）
    seen: set[str] = set()
    items: list[HandoffRecord] = []
    for hid in ids:
        if hid in seen:
            continue
        seen.add(hid)
        rec = state.handoffs.get(hid)
        if rec is not None:
            items.append(HandoffRecord(**rec))
    # 时间倒序（最新的在前）
    items.sort(key=lambda h: h.created_at, reverse=True)
    return HandoffListResponse(session_id=session_id, handoffs=items)


# ── GET /handoffs/{handoff_id} ───────────────────────────────────────────────


@router.get("/handoffs/{handoff_id}", response_model=HandoffRecord)
async def get_handoff(
    handoff_id: str,
    state: AppState = Depends(get_app_state),
) -> HandoffRecord:
    """获取单条 handoff 详情。"""
    rec = state.handoffs.get(handoff_id)
    if rec is None:
        raise HTTPException(
            status_code=404,
            detail=f"Handoff not found: {handoff_id}",
        )
    return HandoffRecord(**rec)
