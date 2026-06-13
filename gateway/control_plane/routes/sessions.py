"""Session CRUD routes for the Daemon API control plane.

Endpoints:
  POST   /sessions          — create session
  GET    /sessions/{id}     — get session detail
  GET    /sessions          — list sessions (paginated)
  DELETE /sessions/{id}     — end / delete session
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from agent.control_plane.ids import new_session_id
from agent.control_plane.store import SessionRecord
from gateway.control_plane.deps import (
    AppState,
    EventBus,
    RuntimeRegistry,
    SessionStore,
    get_app_state,
    get_event_bus,
    get_runtime_registry,
    get_store,
)
from gateway.control_plane.schemas import (
    SessionCreate,
    SessionListResponse,
    SessionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


# ── helpers ──────────────────────────────────────────────────────────────────


def _session_to_response(s: SessionRecord) -> SessionResponse:
    return SessionResponse(
        id=s.id,
        runtime_kind=s.runtime_kind,
        model=s.model,
        repo_path=s.repo_path,
        workspace_id=s.workspace_id,
        status=s.status,
        started_at=s.started_at,
        ended_at=s.ended_at,
        metadata=s.metadata,
    )


# ── POST /sessions ───────────────────────────────────────────────────────────


@router.post("", status_code=201, response_model=SessionResponse)
async def create_session(
    body: SessionCreate,
    store: SessionStore = Depends(get_store),
    state: AppState = Depends(get_app_state),
) -> SessionResponse:
    """Create a new session.

    1. Generate session ID.
    2. (Optional) create workspace via WorkspaceManager.
    3. Persist SessionRecord.
    4. Wave 8.3: if a runtime is registered for ``runtime_kind``,
       call ``runtime.start_session`` and bind session_id ↔ kind.
       Failures are non-fatal — the DB session still exists in 'created'
       state and the caller can retry / debug.
    5. Publish session.started event.
    6. Return session detail.
    """
    sid = new_session_id()

    # Optionally create workspace
    workspace_id: str | None = None
    if body.repo_path:
        try:
            ws = await state.workspace_manager.create_workspace(
                repo_path=body.repo_path,
                base_branch=body.base_branch,
                session_id=sid,
            )
            workspace_id = ws.id
        except Exception as exc:
            logger.warning("workspace creation failed: %s", exc)

    record = SessionRecord(
        id=sid,
        runtime_kind=body.runtime_kind,
        model=body.model,
        repo_path=body.repo_path,
        workspace_id=workspace_id,
        status="created",
        metadata=body.metadata,
    )
    await store.create_session(record)

    # Wave 8.3: 如果 registry 有匹配 runtime，调 start_session
    runtime = state.runtime_registry.get(body.runtime_kind)
    if runtime is not None and body.repo_path:
        try:
            from agent.control_plane.runtimes.interface import (
                StartSessionInput,
            )

            ref = await runtime.start_session(
                StartSessionInput(
                    repo_path=body.repo_path,
                    branch=body.base_branch,
                )
            )
            # 绑定 hermes session_id → runtime kind（turns 路由用）
            state.runtime_registry.bind_session(sid, body.runtime_kind)
            # provider_session_id 落到 metadata（仅内存返回；持久化更新待 store 扩 update_metadata）
            if ref.provider_session_id:
                merged = dict(record.metadata or {})
                merged["provider_session_id"] = ref.provider_session_id
                record.metadata = merged
            # 标记 running（schema 允许的状态：created/running/waiting/completed/failed/cancelled/stopped）
            await store.update_session_status(sid, "running")
            record.status = "running"
            logger.info(
                "[sessions] runtime started kind=%s sid=%s provider_sid=%s",
                body.runtime_kind, sid[:8],
                (ref.provider_session_id or "")[:8],
            )
        except Exception:
            logger.exception(
                "[sessions] runtime.start_session failed kind=%s sid=%s; "
                "DB session kept in 'created'",
                body.runtime_kind, sid[:8],
            )

    # Publish session.started event
    state.event_bus.publish(
        sid,
        {"type": "session.started", "session_id": sid, "timestamp": record.started_at},
    )

    return _session_to_response(record)


# ── GET /sessions/{id} ───────────────────────────────────────────────────────


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    store: SessionStore = Depends(get_store),
) -> SessionResponse:
    """Get session by ID."""
    rec = await store.get_session(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return _session_to_response(rec)


# ── GET /sessions ────────────────────────────────────────────────────────────


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    status: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    store: SessionStore = Depends(get_store),
) -> SessionListResponse:
    """List sessions with optional status filter and pagination."""
    sessions = await store.list_sessions(status=status, limit=limit, offset=offset)
    return SessionListResponse(
        sessions=[_session_to_response(s) for s in sessions],
        total=len(sessions),  # NOTE: not a global total; paginated window
        limit=limit,
        offset=offset,
    )


# ── DELETE /sessions/{id} ────────────────────────────────────────────────────


@router.delete("/{session_id}", response_model=SessionResponse)
async def delete_session(
    session_id: str,
    store: SessionStore = Depends(get_store),
    state: AppState = Depends(get_app_state),
) -> SessionResponse:
    """End / delete a session (marks status as 'stopped')."""
    rec = await store.get_session(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    await store.update_session_status(session_id, "stopped")
    rec.status = "stopped"

    # Cleanup workspace if any
    if rec.workspace_id:
        try:
            await state.workspace_manager.cleanup(rec.workspace_id, force=True)
        except Exception as exc:
            logger.warning("workspace cleanup failed: %s", exc)

    return _session_to_response(rec)
