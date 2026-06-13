"""Event stream routes for the Daemon API control plane.

Endpoints:
  GET       /sessions/{id}/events      — historical event query (paginated)
  WebSocket /sessions/{id}/events/ws   — real-time event push (1 Hz heartbeat)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from agent.control_plane.store import EventRecord, SessionStore
from gateway.control_plane.deps import (
    AppState,
    EventBus,
    get_app_state,
    get_event_bus,
    get_store,
)
from gateway.control_plane.schemas import EventListResponse, EventResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])


# ── helpers ──────────────────────────────────────────────────────────────────


def _event_to_response(e: EventRecord) -> EventResponse:
    return EventResponse(
        id=e.id,
        session_id=e.session_id,
        turn_id=e.turn_id,
        type=e.type,
        payload=e.payload,
        created_at=e.created_at,
    )


# ── GET /sessions/{session_id}/events ────────────────────────────────────────


@router.get(
    "/sessions/{session_id}/events",
    response_model=EventListResponse,
)
async def list_events(
    session_id: str,
    since_id: Optional[int] = Query(None, description="Only events with id > since_id"),
    types: Optional[str] = Query(None, description="Comma-separated event types"),
    limit: int = Query(100, ge=1, le=1000),
    store: SessionStore = Depends(get_store),
) -> EventListResponse:
    """Query historical events for a session.

    Supports incremental polling via ``since_id`` and type filtering via
    ``types`` (comma-separated).
    """
    # Validate session exists
    rec = await store.get_session(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    type_list = [t.strip() for t in types.split(",")] if types else None
    events = await store.list_events(
        session_id, since_id=since_id, types=type_list, limit=limit
    )
    return EventListResponse(
        events=[_event_to_response(e) for e in events],
        limit=limit,
    )


# ── WebSocket /sessions/{session_id}/events/ws ──────────────────────────────

HEARTBEAT_INTERVAL = 1.0  # seconds


@router.websocket("/sessions/{session_id}/events/ws")
async def events_ws(
    websocket: WebSocket,
    session_id: str,
) -> None:
    """Real-time event push via WebSocket.

    Protocol:
      - Client connects to ``ws://.../sessions/{id}/events/ws``
      - Server accepts and subscribes to EventBus for that session
      - Each event is sent as a single JSON line
      - Server sends a ``{"type": "heartbeat"}`` every 1 Hz as keepalive
      - Client disconnect closes the subscription
    """
    # WebSocket routes can't use Depends(Request), pull state directly
    state: AppState = websocket.app.state.cp  # type: ignore[assignment]
    await websocket.accept()

    # Validate session
    if state.store is None:
        await websocket.close(code=1011, reason="Store not initialized")
        return

    rec = await state.store.get_session(session_id)
    if rec is None:
        await websocket.close(code=4004, reason="Session not found")
        return

    q = state.event_bus.subscribe(session_id)
    try:
        while True:
            try:
                # Race between event delivery and heartbeat timeout
                event = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_INTERVAL)
                await websocket.send_text(json.dumps(event))
            except asyncio.TimeoutError:
                # No event within heartbeat interval — send heartbeat
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(json.dumps({"type": "heartbeat"}))
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        state.event_bus.unsubscribe(session_id, q)
