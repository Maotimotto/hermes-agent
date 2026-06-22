"""Pydantic v2 请求/响应 schemas for the Daemon API control plane."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Session schemas ─────────────────────────────────────────────────────────


class SessionCreate(BaseModel):
    """POST /sessions request body."""

    runtime_kind: Literal["codex", "claude"] = "claude"
    model: str | None = None
    repo_path: str | None = None
    base_branch: str = "main"
    metadata: dict[str, Any] | None = None


class SessionResponse(BaseModel):
    """Session detail response."""

    id: str
    runtime_kind: str = "claude"
    model: str | None = None
    repo_path: str | None = None
    workspace_id: str | None = None
    status: str = "created"
    started_at: str = ""
    ended_at: str | None = None
    metadata: dict[str, Any] | None = None


class SessionListResponse(BaseModel):
    """GET /sessions paginated response."""

    sessions: list[SessionResponse]
    total: int
    limit: int
    offset: int


# ── Turn schemas ────────────────────────────────────────────────────────────


class TurnCreate(BaseModel):
    """POST /sessions/{id}/turns request body."""

    prompt: str
    system_prompt: str | None = None
    context_files: dict[str, str] | None = None
    metadata: dict[str, Any] | None = None


class TurnResponse(BaseModel):
    """Turn detail response."""

    turn_id: str
    session_id: str
    status: str = "started"


class TurnInterruptResponse(BaseModel):
    """DELETE /sessions/{sid}/turns/{tid} response."""

    turn_id: str
    status: str = "interrupted"


# ── Event schemas ───────────────────────────────────────────────────────────


class EventResponse(BaseModel):
    """Single event in a list."""

    id: int | None = None
    session_id: str
    turn_id: str | None = None
    type: str
    payload: dict[str, Any]
    created_at: str = ""


class EventListResponse(BaseModel):
    """GET /sessions/{id}/events paginated response."""

    events: list[EventResponse]
    limit: int


# ── Approval schemas ────────────────────────────────────────────────────────


class ApprovalResponse(BaseModel):
    """Single approval record."""

    id: str
    session_id: str
    turn_id: str | None = None
    action_kind: str
    action_payload: dict[str, Any] | None = None
    risk: str | None = None
    decision: str | None = "pending"
    decided_at: str | None = None
    decided_by: str | None = None
    ttl_until: str | None = None


class ApprovalDecisionRequest(BaseModel):
    """POST /approvals/{id}/decision request body."""

    decision: Literal["approved", "denied"]
    ttl: int | None = None  # seconds; remembered decision duration
    decided_by: str | None = None


class ApprovalDecisionResponse(BaseModel):
    """POST /approvals/{id}/decision response."""

    id: str
    decision: str
    decided_at: str


# ── Provider schemas ────────────────────────────────────────────────────────


class ProviderInfo(BaseModel):
    """单个 provider 描述（GET /providers 列表项 / GET /providers/{kind}）。"""

    kind: str                                  # "codex" | "claude" | ...
    available: bool                            # 健康检查结果
    message: str = ""                          # 健康检查描述
    version: str | None = None                 # provider 版本
    latency_ms: int | None = None              # 探活延迟（ms）
    active_sessions: int = 0                   # 绑定到该 runtime 的 session 数


class ProviderListResponse(BaseModel):
    """GET /providers 列表。"""

    providers: list[ProviderInfo]


class ProviderStatusChangeItem(BaseModel):
    """GET /providers/{kind}/history 列表项。"""

    kind: str
    from_available: bool | None = None     # None = 第一次探活
    to_available: bool
    message: str = ""
    at: str = ""                            # ISO timestamp


class ProviderStatusHistoryResponse(BaseModel):
    """GET /providers/{kind}/history 响应。"""

    kind: str
    history: list[ProviderStatusChangeItem]


# ── Workspace schemas ───────────────────────────────────────────────────────


class WorkspaceFileEntry(BaseModel):
    """单个变更文件的摘要。"""

    path: str
    status: str  # "create" | "edit" | "delete" — 与 file.changed 事件一致
    additions: int = 0
    deletions: int = 0


class WorkspaceDiffSummaryResponse(BaseModel):
    """GET /workspaces/{id}/diff 摘要响应。"""

    workspace_id: str
    files: list[WorkspaceFileEntry]
    total_additions: int = 0
    total_deletions: int = 0
    total_files: int = 0


class WorkspaceUnifiedDiffResponse(BaseModel):
    """GET /workspaces/{id}/diff/unified 完整 unified diff 响应。

    ``diffs`` 是 ``{path: unified_diff_text}`` map；``path`` 顺序保持调用 git
    时的顺序，前端可按需排序。
    """

    workspace_id: str
    diffs: dict[str, str]


# ── Handoff schemas（V1.1 P1 Provider 转交）─────────────────────────────────


class HandoffCreate(BaseModel):
    """POST /sessions/{from_session_id}/handoff request body."""

    target_provider: Literal["claude", "codex"]
    # transfer: Claude → Codex 把上下文交给另一个 provider 继续干
    # review:   Codex → Claude 把当前 diff 拿去让 Claude 评审
    kind: Literal["transfer", "review"] = "transfer"
    include_diff: bool = True
    extra_prompt: str | None = None
    # 最近几条 assistant.message 作为上下文摘要（默认 6）
    context_messages: int = Field(default=6, ge=0, le=50)


class HandoffRecord(BaseModel):
    """单条 handoff 记录（GET /handoffs/{id} 及列表项）。"""

    id: str
    created_at: str
    from_session_id: str
    to_session_id: str | None = None
    from_provider: str
    to_provider: str
    kind: Literal["transfer", "review"] = "transfer"
    context_summary: str = ""
    initial_prompt: str = ""
    status: Literal["ok", "failed"] = "ok"
    error: str | None = None


class HandoffResponse(BaseModel):
    """POST /sessions/{id}/handoff 响应。"""

    handoff_id: str
    to_session_id: str | None = None
    from_provider: str
    to_provider: str
    kind: str
    status: str


class HandoffListResponse(BaseModel):
    """GET /sessions/{id}/handoffs 响应。"""

    session_id: str
    handoffs: list[HandoffRecord]


# ── Health schema ───────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    """GET /health response."""

    status: str = "ok"
    store: dict[str, Any] = {}
    workspace: dict[str, Any] = {}
    runtime: dict[str, Any] = {}


# ── Error envelope ──────────────────────────────────────────────────────────


class ErrorBody(BaseModel):
    """统一错误负载。"""

    code: str                          # 机器可读：validation_error / not_found / ...
    message: str                       # 人类可读
    request_id: str | None = None      # 便于排查的相关 ID
    details: Any | None = None         # 可选：FastAPI/Pydantic 校验细节等


class ErrorResponse(BaseModel):
    """统一错误信封：所有非 2xx 都返回这个 shape。"""

    error: ErrorBody
