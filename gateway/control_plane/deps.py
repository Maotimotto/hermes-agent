"""FastAPI dependency injection factories for the Daemon API control plane.

Provides singleton access to:
  - SessionStore  (SQLite-backed, async)
  - WorkspaceManager
  - RuntimeRegistry  (lazy init runtime instances)
  - EventBus  (in-process pub/sub for real-time WS push)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import Depends, Request

from agent.control_plane.approval import ApprovalGate
from agent.control_plane.ids import new_turn_id
from agent.control_plane.store import (
    ApprovalRecord,
    EventRecord,
    SessionRecord,
    SessionStore,
)
from agent.control_plane.workspace import InMemoryWorkspaceStore, WorkspaceManager
from agent.control_plane.runtimes.interface import AgentRuntime, HealthStatus

logger = logging.getLogger(__name__)


# ── EventBus (lightweight in-process pub/sub) ────────────────────────────────


class EventBus:
    """In-process event bus for real-time WebSocket push.

    Subscribers register with `subscribe(session_id)` which returns an
    ``asyncio.Queue``.  Events published via ``publish(event)`` are fanned
    out to all queues subscribed to that session.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    def subscribe(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.setdefault(session_id, []).append(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        subs = self._subscribers.get(session_id)
        if subs is not None:
            try:
                subs.remove(q)
            except ValueError:
                pass
            if not subs:
                del self._subscribers[session_id]

    def publish(self, session_id: str, event: dict[str, Any]) -> None:
        for q in self._subscribers.get(session_id, []):
            q.put_nowait(event)


# ── RuntimeRegistry ──────────────────────────────────────────────────────────


class RuntimeRegistry:
    """Manages AgentRuntime instances.

    Stores active turns so they can be interrupted.  In production the actual
    runtime implementations (CodexAppServerRuntime, ClaudeAgentSdkRuntime) are
    registered; tests inject mocks.
    """

    def __init__(self) -> None:
        self._runtimes: dict[str, AgentRuntime] = {}
        self._active_turns: dict[str, tuple[str, asyncio.Task]] = {}
        # session_id -> runtime kind mapping
        self._session_runtime: dict[str, str] = {}

    def register(self, kind: str, runtime: AgentRuntime) -> None:
        self._runtimes[kind] = runtime

    def get(self, kind: str) -> AgentRuntime | None:
        return self._runtimes.get(kind)

    def bind_session(self, session_id: str, kind: str) -> None:
        self._session_runtime[session_id] = kind

    def get_session_runtime(self, session_id: str) -> str | None:
        return self._session_runtime.get(session_id)

    def register_turn(self, turn_id: str, session_id: str, task: asyncio.Task) -> None:
        self._active_turns[turn_id] = (session_id, task)

    def get_turn_task(self, turn_id: str) -> asyncio.Task | None:
        entry = self._active_turns.get(turn_id)
        return entry[1] if entry else None

    def remove_turn(self, turn_id: str) -> None:
        self._active_turns.pop(turn_id, None)

    async def shutdown(self) -> None:
        """Cancel all active turn tasks."""
        for tid, (_, task) in list(self._active_turns.items()):
            if not task.done():
                task.cancel()
        self._active_turns.clear()


# ── AppState (holds singletons) ─────────────────────────────────────────────


class AppState:
    """Central container for control-plane singletons.

    Created at app startup via ``lifespan`` and attached to ``app.state``.
    """

    def __init__(self) -> None:
        self.store: SessionStore | None = None
        self.event_bus: EventBus = EventBus()
        self.runtime_registry: RuntimeRegistry = RuntimeRegistry()
        self.workspace_manager: WorkspaceManager = WorkspaceManager(
            store=InMemoryWorkspaceStore()
        )
        # ApprovalGate is created lazily after the store finishes init —
        # see ``ensure_approval_gate`` below (called from app lifespan).
        self.approval_gate: ApprovalGate | None = None

    def ensure_approval_gate(self) -> ApprovalGate:
        """Construct the ApprovalGate once the store is ready.

        Idempotent — safe to call multiple times.
        """
        if self.approval_gate is None:
            if self.store is None:
                raise RuntimeError("SessionStore must be initialized before ApprovalGate")
            self.approval_gate = ApprovalGate(self.store, self.event_bus)
        return self.approval_gate


# ── Dependency helpers (used in route signatures) ────────────────────────────


def get_app_state(request: Request) -> AppState:
    """Extract AppState from the request."""
    return request.app.state.cp  # type: ignore[return-value]


def get_store(state: AppState = Depends(get_app_state)) -> SessionStore:
    if state.store is None:
        raise RuntimeError("SessionStore not initialized")
    return state.store


def get_event_bus(state: AppState = Depends(get_app_state)) -> EventBus:
    return state.event_bus


def get_runtime_registry(state: AppState = Depends(get_app_state)) -> RuntimeRegistry:
    return state.runtime_registry


def get_workspace_manager(
    state: AppState = Depends(get_app_state),
) -> WorkspaceManager:
    return state.workspace_manager


def get_approval_gate(state: AppState = Depends(get_app_state)) -> ApprovalGate:
    return state.ensure_approval_gate()
