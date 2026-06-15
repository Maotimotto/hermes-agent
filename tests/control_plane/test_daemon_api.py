"""Tests for the Daemon API control plane.

Uses FastAPI TestClient for synchronous HTTP tests and the built-in
``websocket_connect`` helper for WebSocket tests.  All tests use a
temp SQLite database so no filesystem state leaks.

Covers:
  - test_create_session
  - test_list_sessions
  - test_post_turn
  - test_interrupt_turn
  - test_event_history
  - test_event_ws
  - test_approval_decision
  - test_health
  - test_mount_smoke
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.control_plane.runtimes.interface import (
    AgentRuntime,
    HealthStatus,
    SessionRef,
    StartSessionInput,
    TurnInput,
)
from agent.control_plane.store import (
    ApprovalRecord,
    EventRecord,
    SessionRecord,
    SessionStore,
)
from gateway.control_plane.app import create_control_plane_app, init_store, mount_to
from gateway.control_plane.deps import AppState, RuntimeRegistry


# ── Fixtures ─────────────────────────────────────────────────────────────────


class MockRuntime(AgentRuntime):
    """A mock AgentRuntime for testing.

    Records calls and returns deterministic results.
    """

    def __init__(self) -> None:
        self.started_sessions: list[str] = []
        self.started_turns: list[tuple[str, TurnInput]] = []
        self.interrupted_turns: list[str] = []

    @property
    def provider(self) -> str:  # type: ignore[override]
        return "claude"

    async def start_session(self, input: StartSessionInput) -> SessionRef:
        sid = f"mock_{len(self.started_sessions)}"
        self.started_sessions.append(sid)
        return SessionRef(
            hermes_session_id=sid,
            provider_session_id=sid,
            provider="claude",
        )

    async def start_turn(
        self,
        session_id: str,
        input: TurnInput,
        signal: object | None = None,
    ) -> AsyncIterator[Any]:
        """Yield a single assistant.delta event then complete."""
        from agent.control_plane.hermes_event import (
            AssistantDeltaEvent,
            TurnCompletedEvent,
        )

        self.started_turns.append((session_id, input))
        # NB: real runtimes await on I/O between events, which yields control
        # back to the event loop and lets concurrent DELETE handlers see the
        # turn as still-running. The mock has to mimic that or interrupt
        # tests will lose the race (task removes itself from the registry
        # before the cancel arrives).
        await asyncio.sleep(0.05)
        yield AssistantDeltaEvent(
            session_id=session_id,
            turn_id="mock_turn",
            text="Hello from mock runtime",
        )
        await asyncio.sleep(0.05)
        yield TurnCompletedEvent(
            session_id=session_id,
            turn_id="mock_turn",
            summary="done",
        )

    async def resume_session(self, provider_session_id: str) -> SessionRef:
        return SessionRef(
            hermes_session_id=provider_session_id,
            provider_session_id=provider_session_id,
            provider="claude",
        )

    async def steer_turn(self, turn_id: str, input: str) -> None:
        pass

    async def interrupt_turn(self, turn_id: str) -> None:
        self.interrupted_turns.append(turn_id)

    async def resolve_approval(self, decision: Any) -> None:
        pass

    async def health_check(self) -> HealthStatus:
        return HealthStatus(available=True, message="mock ok", version="0.0.1")


@pytest.fixture()
def mock_runtime() -> MockRuntime:
    return MockRuntime()


@pytest.fixture()
def app_pair(tmp_path: Path, mock_runtime: MockRuntime) -> tuple[FastAPI, FastAPI]:
    """Create a test FastAPI app with control plane mounted.

    Returns ``(main_app, cp_app)`` so tests that need to reach into the
    control-plane state can do so without poking ``client.app.routes[0].app``
    (which is a lazy callable in newer Starlette).
    """
    db_path = tmp_path / "test.db"
    cp = create_control_plane_app(db_path=str(db_path), run_migrations=True)

    # Explicitly init the store (on_event("startup") doesn't fire for mounted sub-apps in TestClient)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_store(cp))
    loop.close()

    # Register mock runtime
    state: AppState = cp.state.cp  # type: ignore[assignment]
    state.runtime_registry.register("claude", mock_runtime)
    state.runtime_registry.register("codex", mock_runtime)

    # Create a wrapper FastAPI app and mount
    main_app = FastAPI()
    main_app.mount("/control-plane", cp)
    # Expose cp on the main app for tests that need to reach into state
    main_app.state.cp_app = cp  # type: ignore[attr-defined]
    return main_app, cp


@pytest.fixture()
def app(app_pair: tuple[FastAPI, FastAPI]) -> FastAPI:
    return app_pair[0]


@pytest.fixture()
def cp_app(app_pair: tuple[FastAPI, FastAPI]) -> FastAPI:
    return app_pair[1]


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    """Synchronous TestClient."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── Session tests ────────────────────────────────────────────────────────────


class TestSessions:
    def test_create_session(self, client: TestClient) -> None:
        resp = client.post(
            "/control-plane/sessions",
            json={"runtime_kind": "claude", "model": "claude-sonnet-4-6"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"].startswith("sess_")
        assert data["runtime_kind"] == "claude"
        assert data["model"] == "claude-sonnet-4-6"
        assert data["status"] == "created"

    def test_create_session_minimal(self, client: TestClient) -> None:
        resp = client.post("/control-plane/sessions", json={})
        assert resp.status_code == 201
        data = resp.json()
        assert data["runtime_kind"] == "claude"

    def test_get_session(self, client: TestClient) -> None:
        create = client.post("/control-plane/sessions", json={})
        sid = create.json()["id"]
        resp = client.get(f"/control-plane/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["id"] == sid

    def test_get_session_not_found(self, client: TestClient) -> None:
        resp = client.get("/control-plane/sessions/nonexistent")
        assert resp.status_code == 404

    def test_list_sessions(self, client: TestClient) -> None:
        # Create two sessions
        client.post("/control-plane/sessions", json={"runtime_kind": "claude"})
        client.post("/control-plane/sessions", json={"runtime_kind": "codex"})

        resp = client.get("/control-plane/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2
        assert len(data["sessions"]) >= 2

    def test_list_sessions_with_status_filter(self, client: TestClient) -> None:
        create = client.post("/control-plane/sessions", json={})
        sid = create.json()["id"]
        # Delete (stop) the session
        client.delete(f"/control-plane/sessions/{sid}")

        resp = client.get("/control-plane/sessions?status=stopped")
        assert resp.status_code == 200
        ids = [s["id"] for s in resp.json()["sessions"]]
        assert sid in ids

    def test_delete_session(self, client: TestClient) -> None:
        create = client.post("/control-plane/sessions", json={})
        sid = create.json()["id"]
        resp = client.delete(f"/control-plane/sessions/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

    def test_delete_session_not_found(self, client: TestClient) -> None:
        resp = client.delete("/control-plane/sessions/nonexistent")
        assert resp.status_code == 404


# ── Turn tests ───────────────────────────────────────────────────────────────


class TestTurns:
    def test_post_turn(self, client: TestClient, mock_runtime: MockRuntime) -> None:
        create = client.post("/control-plane/sessions", json={"runtime_kind": "claude"})
        sid = create.json()["id"]
        resp = client.post(
            f"/control-plane/sessions/{sid}/turns",
            json={"prompt": "Hello, agent!"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["turn_id"].startswith("turn_")
        assert data["session_id"] == sid
        assert data["status"] == "started"

        # Give the background task a moment to run
        time.sleep(0.3)

    def test_post_turn_session_not_found(self, client: TestClient) -> None:
        resp = client.post(
            "/control-plane/sessions/nonexistent/turns",
            json={"prompt": "Hello"},
        )
        assert resp.status_code == 404

    def test_interrupt_turn(self, client: TestClient, mock_runtime: MockRuntime) -> None:
        create = client.post("/control-plane/sessions", json={"runtime_kind": "claude"})
        sid = create.json()["id"]

        turn_resp = client.post(
            f"/control-plane/sessions/{sid}/turns",
            json={"prompt": "Long task"},
        )
        tid = turn_resp.json()["turn_id"]

        resp = client.delete(f"/control-plane/sessions/{sid}/turns/{tid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["turn_id"] == tid
        assert data["status"] == "interrupted"

    def test_interrupt_turn_not_found(self, client: TestClient) -> None:
        create = client.post("/control-plane/sessions", json={})
        sid = create.json()["id"]
        resp = client.delete(f"/control-plane/sessions/{sid}/turns/turn_nonexistent")
        assert resp.status_code == 404


# ── Event tests ──────────────────────────────────────────────────────────────


class TestEvents:
    def test_event_history(self, client: TestClient) -> None:
        # Create session
        create = client.post("/control-plane/sessions", json={})
        sid = create.json()["id"]

        # Query events via REST
        resp = client.get(f"/control-plane/sessions/{sid}/events")
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data
        assert data["limit"] == 100

    def test_event_history_not_found(self, client: TestClient) -> None:
        resp = client.get("/control-plane/sessions/nonexistent/events")
        assert resp.status_code == 404

    def test_event_ws(self, client: TestClient) -> None:
        """Test WebSocket real-time event push.

        1. Create a session.
        2. Connect WS to that session's events.
        3. Read one frame — should be a heartbeat within ~1.5s.
        """
        create = client.post("/control-plane/sessions", json={})
        sid = create.json()["id"]

        with client.websocket_connect(
            f"/control-plane/sessions/{sid}/events/ws"
        ) as ws:
            # First frame must arrive within HEARTBEAT_INTERVAL (1.0s) — be generous.
            raw = ws.receive_text()
            msg = json.loads(raw)
            assert msg.get("type") == "heartbeat"

    def test_event_ws_session_not_found(self, client: TestClient) -> None:
        """WebSocket with non-existent session should close."""
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/control-plane/sessions/nonexistent/events/ws"
            ) as ws:
                ws.receive_text(timeout=1.0)


# ── Approval tests ───────────────────────────────────────────────────────────


class TestApprovals:
    def _seed_approval(self, client: TestClient, cp_app: FastAPI) -> tuple[str, str]:
        """Create a session and insert an approval record directly."""
        create = client.post("/control-plane/sessions", json={})
        sid = create.json()["id"]

        state: AppState = cp_app.state.cp  # type: ignore[assignment]
        store = state.store

        aid = f"apr_test_{sid[-6:]}"
        rec = ApprovalRecord(
            id=aid,
            session_id=sid,
            action_kind="shell.command",
            action_payload={"command": "rm -rf /tmp/test"},
            risk="high",
            decision="pending",
        )
        loop = asyncio.new_event_loop()
        loop.run_until_complete(store.record_request(rec))
        loop.close()
        return sid, aid

    def test_approval_decision(self, client: TestClient, cp_app: FastAPI) -> None:
        sid, aid = self._seed_approval(client, cp_app)

        resp = client.post(
            f"/control-plane/approvals/{aid}/decision",
            json={"decision": "approved", "ttl": 3600, "decided_by": "user"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == aid
        assert data["decision"] == "approved"
        assert "decided_at" in data

    def test_approval_decision_denied(self, client: TestClient, cp_app: FastAPI) -> None:
        sid, aid = self._seed_approval(client, cp_app)

        resp = client.post(
            f"/control-plane/approvals/{aid}/decision",
            json={"decision": "denied", "decided_by": "user"},
        )
        assert resp.status_code == 200
        assert resp.json()["decision"] == "denied"

    def test_approval_decision_not_found(self, client: TestClient) -> None:
        resp = client.post(
            "/control-plane/approvals/nonexistent/decision",
            json={"decision": "approved"},
        )
        assert resp.status_code == 404

    def test_list_approvals(self, client: TestClient, cp_app: FastAPI) -> None:
        self._seed_approval(client, cp_app)

        resp = client.get("/control-plane/approvals?status=pending")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["decision"] == "pending"


# ── Health test ──────────────────────────────────────────────────────────────


class TestHealth:
    def test_health(self, client: TestClient) -> None:
        resp = client.get("/control-plane/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "degraded")
        assert "store" in data
        assert "workspace" in data
        assert "runtime" in data
        assert data["store"]["status"] == "ok"


# ── Mount smoke test ─────────────────────────────────────────────────────────


class TestMountSmoke:
    def test_mount_to_existing_app(self, tmp_path: Path) -> None:
        """Verify mount_to() does not raise and routes are accessible."""
        db_path = tmp_path / "smoke.db"

        main_app = FastAPI()
        cp_app = create_control_plane_app(db_path=str(db_path))
        main_app.mount("/control-plane", cp_app)

        with TestClient(main_app) as client:
            # Manually init store since on_event may not fire in TestClient for sub-apps
            loop = asyncio.new_event_loop()
            loop.run_until_complete(init_store(cp_app))
            loop.close()

            resp = client.get("/control-plane/health")
            assert resp.status_code == 200

    def test_mount_to_helper(self, tmp_path: Path) -> None:
        """Verify the mount_to() convenience function works."""
        main_app = FastAPI()
        mount_to(main_app, db_path=str(tmp_path / "mount.db"))

        # mount_to creates the sub-app internally; reach it via routes
        # (in Starlette, Mount.app is the actual sub-application instance)
        from starlette.routing import Mount

        cp_app = next(r.app for r in main_app.routes if isinstance(r, Mount))

        with TestClient(main_app) as client:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(init_store(cp_app))
            loop.close()

            resp = client.get("/control-plane/health")
            assert resp.status_code == 200


# ── EventBus unit test ───────────────────────────────────────────────────────


class TestEventBus:
    def test_subscribe_publish_unsubscribe(self) -> None:
        from gateway.control_plane.deps import EventBus

        bus = EventBus()
        q = bus.subscribe("sess_1")

        # Publish an event
        bus.publish("sess_1", {"type": "test", "data": 42})
        assert not q.empty()
        item = q.get_nowait()
        assert item["type"] == "test"

        # Unsubscribe
        bus.unsubscribe("sess_1", q)
        bus.publish("sess_1", {"type": "test2"})
        assert q.empty()  # no new items after unsubscribe

    def test_publish_no_subscribers(self) -> None:
        from gateway.control_plane.deps import EventBus

        bus = EventBus()
        # Should not raise
        bus.publish("sess_999", {"type": "orphan"})


# ── RuntimeRegistry unit test ───────────────────────────────────────────────


class TestRuntimeRegistry:
    def test_register_and_get(self, mock_runtime: MockRuntime) -> None:
        from gateway.control_plane.deps import RuntimeRegistry

        reg = RuntimeRegistry()
        reg.register("claude", mock_runtime)
        assert reg.get("claude") is mock_runtime
        assert reg.get("codex") is None

    def test_session_binding(self, mock_runtime: MockRuntime) -> None:
        from gateway.control_plane.deps import RuntimeRegistry

        reg = RuntimeRegistry()
        reg.bind_session("sess_1", "claude")
        assert reg.get_session_runtime("sess_1") == "claude"
        assert reg.get_session_runtime("sess_2") is None
