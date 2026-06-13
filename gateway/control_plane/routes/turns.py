"""Turn management routes for the Daemon API control plane.

Endpoints:
  POST   /sessions/{id}/turns          — start a turn
  DELETE /sessions/{sid}/turns/{tid}    — interrupt a running turn
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from agent.control_plane.ids import new_turn_id
from agent.control_plane.runtimes.interface import TurnInput
from agent.control_plane.store import EventRecord, SessionStore
from gateway.control_plane.deps import (
    AppState,
    EventBus,
    RuntimeRegistry,
    get_app_state,
    get_event_bus,
    get_runtime_registry,
    get_store,
)
from gateway.control_plane.schemas import (
    TurnCreate,
    TurnInterruptResponse,
    TurnResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["turns"])


async def _insert_turn_record(store: SessionStore, turn_id: str, session_id: str, prompt: str) -> None:
    """Insert a row into the turns table so FK constraints on events.turn_id are satisfied."""
    now = datetime.now(timezone.utc).isoformat()
    await store.db.execute(
        """INSERT INTO turns (id, session_id, prompt, status, started_at)
           VALUES (?, ?, ?, 'running', ?)""",
        (turn_id, session_id, prompt, now),
    )
    await store.db.commit()


# ── POST /sessions/{session_id}/turns ────────────────────────────────────────


@router.post(
    "/sessions/{session_id}/turns",
    status_code=202,
    response_model=TurnResponse,
)
async def create_turn(
    session_id: str,
    body: TurnCreate,
    store: SessionStore = Depends(get_store),
    state: AppState = Depends(get_app_state),
) -> TurnResponse:
    """Start a new turn within a session.

    1. Validate session exists.
    2. Generate turn ID.
    3. Insert turn record into turns table (for FK constraint).
    4. Persist turn.started event.
    5. Start runtime turn in background task (event-driven).
    6. Return immediately with 202.
    """
    rec = await store.get_session(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    tid = new_turn_id()

    # Insert turn record for FK constraint
    await _insert_turn_record(store, tid, session_id, body.prompt)

    # Record turn.started event
    await store.append_event(
        EventRecord(
            session_id=session_id,
            turn_id=tid,
            type="turn.started",
            payload={"prompt": body.prompt},
        )
    )

    # Publish to event bus
    state.event_bus.publish(
        session_id,
        {"type": "turn.started", "session_id": session_id, "turn_id": tid},
    )

    # Determine runtime kind
    kind = state.runtime_registry.get_session_runtime(session_id) or rec.runtime_kind
    runtime = state.runtime_registry.get(kind)

    if runtime is not None:
        # Fire-and-forget background task to run the turn
        turn_input = TurnInput(
            prompt=body.prompt,
            system_prompt=body.system_prompt,
            context_files=body.context_files,
            metadata=body.metadata,
        )

        async def _run_turn() -> None:
            try:
                async for event in runtime.start_turn(session_id, turn_input):
                    event_dict = event.model_dump(mode="json")
                    state.event_bus.publish(session_id, event_dict)
                    await store.append_event(
                        EventRecord(
                            session_id=session_id,
                            turn_id=tid,
                            type=event.type,
                            payload=event_dict,
                        )
                    )
            except asyncio.CancelledError:
                state.event_bus.publish(
                    session_id,
                    {
                        "type": "turn.cancelled",
                        "session_id": session_id,
                        "turn_id": tid,
                    },
                )
                await store.append_event(
                    EventRecord(
                        session_id=session_id,
                        turn_id=tid,
                        type="turn.cancelled",
                        payload={"reason": "cancelled"},
                    )
                )
            except Exception as exc:
                logger.exception("turn %s failed", tid)
                state.event_bus.publish(
                    session_id,
                    {
                        "type": "turn.failed",
                        "session_id": session_id,
                        "turn_id": tid,
                        "error": str(exc),
                    },
                )
                await store.append_event(
                    EventRecord(
                        session_id=session_id,
                        turn_id=tid,
                        type="turn.failed",
                        payload={"error": str(exc)},
                    )
                )
            finally:
                state.runtime_registry.remove_turn(tid)

        task = asyncio.create_task(_run_turn())
        state.runtime_registry.register_turn(tid, session_id, task)
    else:
        # No runtime registered — log a warning but still accept the turn
        logger.warning("No runtime registered for kind=%s; turn accepted but not executed", kind)

    return TurnResponse(turn_id=tid, session_id=session_id, status="started")


# ── DELETE /sessions/{sid}/turns/{tid} ────────────────────────────────────────


@router.delete(
    "/sessions/{session_id}/turns/{turn_id}",
    response_model=TurnInterruptResponse,
)
async def interrupt_turn(
    session_id: str,
    turn_id: str,
    store: SessionStore = Depends(get_store),
    state: AppState = Depends(get_app_state),
) -> TurnInterruptResponse:
    """Interrupt a running turn.

    Cancels the background asyncio task and notifies the runtime.
    """
    # Validate session exists
    rec = await store.get_session(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    task = state.runtime_registry.get_turn_task(turn_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Turn not found or already finished: {turn_id}")

    # Try runtime interrupt
    kind = state.runtime_registry.get_session_runtime(session_id)
    if kind:
        runtime = state.runtime_registry.get(kind)
        if runtime:
            try:
                await runtime.interrupt_turn(turn_id)
            except Exception as exc:
                logger.warning("runtime interrupt failed: %s", exc)

    # Cancel the asyncio task
    if not task.done():
        task.cancel()

    # Record event
    await store.append_event(
        EventRecord(
            session_id=session_id,
            turn_id=turn_id,
            type="turn.cancelled",
            payload={"reason": "user_interrupt"},
        )
    )
    state.event_bus.publish(
        session_id,
        {"type": "turn.cancelled", "session_id": session_id, "turn_id": turn_id},
    )

    return TurnInterruptResponse(turn_id=turn_id, status="interrupted")
