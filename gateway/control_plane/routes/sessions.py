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
    2. Persist SessionRecord.
    3. (Future) create workspace via WorkspaceManager.
    4. Return session detail.
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
