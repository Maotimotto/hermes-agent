"""Wave B 错误恢复 — turns 路由自动重试集成测试。

验证 _run_turn 在以下三种场景下的事件序列：

1. 首次 turn.failed (retryable=True) → 应产出 turn.retrying 后再起一次，第二次成功
   则收尾 turn.completed，整段 timeline 不应出现 turn.failed。
2. 首次失败 → 重试再失败：第二次的 turn.failed 应透传给 EventBus（不再 retrying）。
3. 首次失败但 retryable=False（如 auth）：直接透传 turn.failed，不应有 turn.retrying。
4. start_turn 抛 Python 异常（runtime 没接住）：路由层 classifier 兜底，TimeoutError
   应触发一次重试。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import AsyncIterator, Iterable
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from agent.control_plane.hermes_event import (
    HermesEvent,
    SessionStartedEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)
from agent.control_plane.runtimes.interface import (
    AgentRuntime,
    SessionRef,
)
from gateway.control_plane.app import create_control_plane_app
from gateway.control_plane.deps import AppState


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        yield path
    finally:
        if os.path.exists(path):
            os.unlink(path)


def _make_runtime_yielding(scripts: list[list[HermesEvent]]) -> MagicMock:
    """构造一个 runtime mock：每次调 start_turn 产出 scripts[i] 这一段事件流。

    scripts 是「每次调用产出的事件列表」的列表，长度 = 期望被调的次数。
    """
    rt = MagicMock(spec=AgentRuntime)
    rt.start_session = AsyncMock(
        return_value=SessionRef(
            hermes_session_id="",
            provider_session_id="prov_abc",
            provider="claude",
        )
    )
    rt.interrupt_turn = AsyncMock()

    call_index = {"n": 0}

    def _start_turn(session_id: str, turn_input, signal=None) -> AsyncIterator[HermesEvent]:
        idx = call_index["n"]
        call_index["n"] += 1
        events = scripts[idx] if idx < len(scripts) else []

        async def _gen() -> AsyncIterator[HermesEvent]:
            for e in events:
                yield e

        return _gen()

    rt.start_turn = _start_turn
    rt._call_index = call_index  # 暴露给测试断言
    return rt


def _create_session_and_get_id(client: TestClient) -> str:
    r = client.post(
        "/sessions",
        json={
            "runtime_kind": "claude",
            "model": "claude-opus-4",
            "repo_path": "/tmp/repo",
            "base_branch": "main",
            "metadata": {},
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _drain_events(client: TestClient, session_id: str, *, timeout: float = 3.0) -> list[dict]:
    """轮询 GET /sessions/{id}/events 直到拿到 turn.completed/turn.failed 终止事件。"""
    import time

    deadline = time.time() + timeout
    seen: list[dict] = []
    seen_ids: set[str] = set()
    while time.time() < deadline:
        r = client.get(f"/sessions/{session_id}/events")
        assert r.status_code == 200
        body = r.json()
        events_list = body.get("events", body) if isinstance(body, dict) else body
        for evt in events_list:
            eid = evt.get("id")
            if eid and eid in seen_ids:
                continue
            if eid:
                seen_ids.add(eid)
            seen.append(evt)
        if any(e["type"] in {"turn.completed", "turn.failed", "turn.cancelled"} for e in seen):
            return seen
        time.sleep(0.05)
    return seen


# ── 场景 1：失败 → retry → 成功 ──────────────────────────────────


class TestRetryThenSucceed:
    def test_retryable_failure_triggers_retry_and_completes(self, db_path):
        # runtime 第一次 start_turn 产出 turn.failed(retryable)，第二次产出 turn.completed
        scripts = [
            [
                TurnStartedEvent(session_id="s", turn_id="t"),
                TurnFailedEvent(
                    session_id="s",
                    turn_id="t",
                    error="connection refused",
                    code="network",
                    retryable=True,
                ),
            ],
            [
                TurnStartedEvent(session_id="s", turn_id="t"),
                TurnCompletedEvent(session_id="s", turn_id="t"),
            ],
        ]
        rt = _make_runtime_yielding(scripts)
        # 把 retry_after_ms 缩短：用 monkey-patch 默认表
        from agent.control_plane import error_recovery as er

        original = dict(er.DEFAULT_RETRY_AFTER_MS)
        er.DEFAULT_RETRY_AFTER_MS[er.ErrorCategory.network] = 0
        try:
            app = create_control_plane_app(db_path=db_path, runtime_configs=None)
            with TestClient(app) as client:
                client.get("/health")
                state: AppState = app.state.cp
                state.runtime_registry.register("claude", rt)

                sid = _create_session_and_get_id(client)
                r = client.post(f"/sessions/{sid}/turns", json={"prompt": "hi"})
                assert r.status_code == 202

                events = _drain_events(client, sid, timeout=5.0)
                types = [e["type"] for e in events]

                # 期望：至少看见 turn.retrying（吞掉的 turn.failed 不出现）+ turn.completed
                assert "turn.retrying" in types, types
                assert "turn.completed" in types, types
                assert "turn.failed" not in types, types

                # runtime.start_turn 被调两次
                assert rt._call_index["n"] == 2

                # turn.retrying 字段对齐（在 payload 子字典）
                retry_evt = next(e for e in events if e["type"] == "turn.retrying")
                payload = retry_evt.get("payload", retry_evt)
                assert payload["attempt"] == 2
                assert payload["reason"] == "network"
        finally:
            er.DEFAULT_RETRY_AFTER_MS.clear()
            er.DEFAULT_RETRY_AFTER_MS.update(original)


# ── 场景 2：失败 → retry → 仍失败 → 终止 ─────────────────────────


class TestRetryThenFailAgain:
    def test_second_failure_propagates_turn_failed(self, db_path):
        scripts = [
            [
                TurnStartedEvent(session_id="s", turn_id="t"),
                TurnFailedEvent(
                    session_id="s",
                    turn_id="t",
                    error="HTTP 503 overloaded",
                    code="overloaded",
                    retryable=True,
                ),
            ],
            [
                TurnStartedEvent(session_id="s", turn_id="t"),
                TurnFailedEvent(
                    session_id="s",
                    turn_id="t",
                    error="HTTP 503 overloaded",
                    code="overloaded",
                    retryable=True,
                ),
            ],
        ]
        rt = _make_runtime_yielding(scripts)
        from agent.control_plane import error_recovery as er

        original = dict(er.DEFAULT_RETRY_AFTER_MS)
        er.DEFAULT_RETRY_AFTER_MS[er.ErrorCategory.overloaded] = 0
        try:
            app = create_control_plane_app(db_path=db_path, runtime_configs=None)
            with TestClient(app) as client:
                client.get("/health")
                state: AppState = app.state.cp
                state.runtime_registry.register("claude", rt)

                sid = _create_session_and_get_id(client)
                client.post(f"/sessions/{sid}/turns", json={"prompt": "hi"})

                events = _drain_events(client, sid, timeout=5.0)
                types = [e["type"] for e in events]

                # 期望：恰好一次 retrying + 最终 turn.failed
                assert types.count("turn.retrying") == 1
                assert "turn.failed" in types
                # turn.failed 在 turn.retrying 之后
                assert types.index("turn.failed") > types.index("turn.retrying")
                assert rt._call_index["n"] == 2
        finally:
            er.DEFAULT_RETRY_AFTER_MS.clear()
            er.DEFAULT_RETRY_AFTER_MS.update(original)


# ── 场景 3：不可重试 → 直接终止 ──────────────────────────────────


class TestNonRetryableFailure:
    def test_auth_failure_no_retry(self, db_path):
        scripts = [
            [
                TurnStartedEvent(session_id="s", turn_id="t"),
                TurnFailedEvent(
                    session_id="s",
                    turn_id="t",
                    error="HTTP 401 invalid api key",
                    code="auth",
                    retryable=False,
                ),
            ],
        ]
        rt = _make_runtime_yielding(scripts)
        app = create_control_plane_app(db_path=db_path, runtime_configs=None)
        with TestClient(app) as client:
            client.get("/health")
            state: AppState = app.state.cp
            state.runtime_registry.register("claude", rt)

            sid = _create_session_and_get_id(client)
            client.post(f"/sessions/{sid}/turns", json={"prompt": "hi"})

            events = _drain_events(client, sid, timeout=3.0)
            types = [e["type"] for e in events]

            assert "turn.retrying" not in types
            assert "turn.failed" in types
            assert rt._call_index["n"] == 1


# ── 场景 4：runtime 抛 Python 异常 → classifier 兜底 ───────────────


class TestPythonExceptionFallback:
    def test_timeout_exception_triggers_retry(self, db_path):
        # 第一次 yield 一个事件后抛 TimeoutError；第二次正常完成
        from agent.control_plane.hermes_event import TurnCompletedEvent

        rt = MagicMock(spec=AgentRuntime)
        rt.start_session = AsyncMock(
            return_value=SessionRef(
                hermes_session_id="",
                provider_session_id="prov_abc",
                provider="claude",
            )
        )
        rt.interrupt_turn = AsyncMock()
        call_index = {"n": 0}

        def _start_turn(session_id, turn_input, signal=None):
            idx = call_index["n"]
            call_index["n"] += 1

            async def _gen():
                yield TurnStartedEvent(session_id="s", turn_id="t")
                if idx == 0:
                    raise asyncio.TimeoutError()
                yield TurnCompletedEvent(session_id="s", turn_id="t")

            return _gen()

        rt.start_turn = _start_turn
        rt._call_index = call_index

        from agent.control_plane import error_recovery as er

        original = dict(er.DEFAULT_RETRY_AFTER_MS)
        er.DEFAULT_RETRY_AFTER_MS[er.ErrorCategory.timeout] = 0
        try:
            app = create_control_plane_app(db_path=db_path, runtime_configs=None)
            with TestClient(app) as client:
                client.get("/health")
                state: AppState = app.state.cp
                state.runtime_registry.register("claude", rt)

                sid = _create_session_and_get_id(client)
                client.post(f"/sessions/{sid}/turns", json={"prompt": "hi"})

                events = _drain_events(client, sid, timeout=5.0)
                types = [e["type"] for e in events]

                assert "turn.retrying" in types
                assert "turn.completed" in types
                assert "turn.failed" not in types
                assert rt._call_index["n"] == 2

                retry_evt = next(e for e in events if e["type"] == "turn.retrying")
                payload = retry_evt.get("payload", retry_evt)
                assert payload["reason"] == "timeout"
        finally:
            er.DEFAULT_RETRY_AFTER_MS.clear()
            er.DEFAULT_RETRY_AFTER_MS.update(original)
