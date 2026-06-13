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


# ── Health schema ───────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    """GET /health response."""

    status: str = "ok"
    store: dict[str, Any] = {}
    workspace: dict[str, Any] = {}
    runtime: dict[str, Any] = {}
