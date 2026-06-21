"""ProviderHealthMonitor + provider 健康门禁测试（V1.1 错误恢复 Wave C）。

覆盖点：
1. ProviderHealthMonitor 单元 — probe / 状态翻转记录 / 历史 / 后台 loop / 启停
2. /providers 路由 — 优先读 monitor 缓存
3. /providers/{kind}/history — 状态变化历史端点
4. POST /sessions/{sid}/turns — provider unavailable 时返回 503
5. 探活异常时门禁不拦截（fail-open，避免 health_check 自身 bug 让全站瘫痪）
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import AsyncIterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from agent.control_plane.hermes_event import HermesEvent
from agent.control_plane.provider_health import (
    ProviderHealthMonitor,
    ProviderHealthSnapshot,
    ProviderStatusChange,
)
from agent.control_plane.runtimes.interface import (
    AgentRuntime,
    HealthStatus,
    SessionRef,
    StartSessionInput,
    TurnInput,
)
from gateway.control_plane.app import create_control_plane_app as build_app
from gateway.control_plane.deps import RuntimeRegistry


# ── Fake runtimes ───────────────────────────────────────────────────


class _FakeRuntime(AgentRuntime):
    """可控的 runtime，按队列返回 health_check 结果。"""

    def __init__(self, kind: str = "fake") -> None:
        self.kind = kind
        self._next_results: list[HealthStatus | Exception] = []
        self.probe_calls = 0

    def push(self, result: HealthStatus | Exception) -> None:
        self._next_results.append(result)

    @property
    def provider(self):
        return "claude"  # type: ignore[return-value]

    async def health_check(self) -> HealthStatus:
        self.probe_calls += 1
        if not self._next_results:
            return HealthStatus(available=True, message="default")
        result = self._next_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    # AgentRuntime 抽象方法兜底（测试不会用到）
    async def start_session(self, input: StartSessionInput) -> SessionRef:  # pragma: no cover
        return SessionRef(provider_session_id="fake", runtime_kind=self.kind)

    async def start_turn(  # pragma: no cover
        self, session_id: str, turn_input: TurnInput
    ):
        if False:
            yield  # type: ignore[unreachable]

    async def resume_session(  # pragma: no cover
        self, provider_session_id: str
    ) -> SessionRef:
        return SessionRef(
            provider_session_id=provider_session_id, runtime_kind=self.kind
        )

    async def steer_turn(self, turn_id: str, input: str) -> None:  # pragma: no cover
        return None

    async def interrupt_turn(self, turn_id: str) -> None:  # pragma: no cover
        return None

    async def resolve_approval(self, decision) -> None:  # pragma: no cover
        return None


# ── 单元：ProviderHealthMonitor ─────────────────────────────────────


class TestProviderHealthMonitor:
    @pytest.mark.asyncio
    async def test_probe_one_records_snapshot(self):
        registry = RuntimeRegistry()
        rt = _FakeRuntime("alpha")
        rt.push(HealthStatus(available=True, message="ok"))
        registry.register("alpha", rt)
        mon = ProviderHealthMonitor(registry, poll_interval_sec=10)

        snap = await mon.probe_one("alpha")
        assert snap.available is True
        assert snap.message == "ok"
        assert snap.error is None
        assert snap.probe_duration_ms >= 0
        assert mon.snapshot("alpha") is snap

    @pytest.mark.asyncio
    async def test_probe_one_unknown_kind_raises(self):
        mon = ProviderHealthMonitor(RuntimeRegistry())
        with pytest.raises(KeyError):
            await mon.probe_one("nope")

    @pytest.mark.asyncio
    async def test_health_check_exception_marks_unavailable(self):
        registry = RuntimeRegistry()
        rt = _FakeRuntime("beta")
        rt.push(RuntimeError("boom"))
        registry.register("beta", rt)
        mon = ProviderHealthMonitor(registry)

        snap = await mon.probe_one("beta")
        assert snap.available is False
        assert "boom" in snap.message
        assert snap.error == "boom"

    @pytest.mark.asyncio
    async def test_status_transition_recorded(self):
        registry = RuntimeRegistry()
        rt = _FakeRuntime("gamma")
        registry.register("gamma", rt)
        mon = ProviderHealthMonitor(registry)

        rt.push(HealthStatus(available=True, message="ok"))
        await mon.probe_one("gamma")
        rt.push(HealthStatus(available=False, message="down"))
        await mon.probe_one("gamma")
        rt.push(HealthStatus(available=True, message="back"))
        await mon.probe_one("gamma")

        history = mon.history("gamma")
        # 首次 None→True、第二次 True→False、第三次 False→True
        assert len(history) == 3
        assert history[0].from_available is None and history[0].to_available is True
        assert history[1].from_available is True and history[1].to_available is False
        assert history[2].from_available is False and history[2].to_available is True

    @pytest.mark.asyncio
    async def test_no_change_no_history(self):
        registry = RuntimeRegistry()
        rt = _FakeRuntime("delta")
        registry.register("delta", rt)
        mon = ProviderHealthMonitor(registry)

        rt.push(HealthStatus(available=True, message="ok"))
        await mon.probe_one("delta")
        rt.push(HealthStatus(available=True, message="ok"))
        await mon.probe_one("delta")
        # 状态没翻转，history 只有首次那条
        assert len(mon.history("delta")) == 1

    @pytest.mark.asyncio
    async def test_probe_all_iterates_registry(self):
        registry = RuntimeRegistry()
        rt1 = _FakeRuntime("one")
        rt2 = _FakeRuntime("two")
        rt1.push(HealthStatus(available=True, message="one ok"))
        rt2.push(HealthStatus(available=False, message="two down"))
        registry.register("one", rt1)
        registry.register("two", rt2)
        mon = ProviderHealthMonitor(registry)

        results = await mon.probe_all()
        assert set(results.keys()) == {"one", "two"}
        assert results["one"].available is True
        assert results["two"].available is False

    @pytest.mark.asyncio
    async def test_start_stop_runs_periodically(self):
        registry = RuntimeRegistry()
        rt = _FakeRuntime("looped")
        # 给一连串 ok 让 loop 跑几轮
        for _ in range(10):
            rt.push(HealthStatus(available=True, message="ok"))
        registry.register("looped", rt)

        mon = ProviderHealthMonitor(registry, poll_interval_sec=0.05)
        await mon.start()
        # start 自身会探一次（=1 次），随后约每 50ms 再一次
        await asyncio.sleep(0.18)
        await mon.stop()
        assert rt.probe_calls >= 2
        # stop 后不再增长
        before = rt.probe_calls
        await asyncio.sleep(0.1)
        assert rt.probe_calls == before


# ── HTTP：/providers 路由 ────────────────────────────────────────────


@pytest_asyncio.fixture
async def daemon_app():
    """构建一个不依赖默认 runtime 配置的 daemon app，注入 fake runtime。"""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    # poll 设小一点，但不靠它驱动 — 测试里直接调 probe_one
    os.environ["HERMES_HEALTH_POLL_SEC"] = "60"
    try:
        app = build_app(db_path=db_path, runtime_configs=None)
        with TestClient(app) as client:
            # 启动后注入 fake runtime（lifespan 已跑完，monitor 没启动是因为
            # registry 此刻为空）— 我们手动启 monitor。
            state = app.state.cp
            rt = _FakeRuntime("claude")
            state.runtime_registry.register("claude", rt)
            monitor = ProviderHealthMonitor(
                state.runtime_registry, poll_interval_sec=60
            )
            state.provider_health = monitor
            yield client, app, rt, monitor
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


class TestProvidersRoute:
    @pytest.mark.asyncio
    async def test_list_uses_cache_when_available(self, daemon_app):
        client, _app, rt, monitor = daemon_app
        rt.push(HealthStatus(available=True, message="cached"))
        await monitor.probe_one("claude")
        # 路由读缓存：不应触发新一次 health_check
        before = rt.probe_calls
        r = client.get("/providers")
        assert r.status_code == 200
        body = r.json()
        assert len(body["providers"]) == 1
        assert body["providers"][0]["available"] is True
        assert body["providers"][0]["message"] == "cached"
        assert rt.probe_calls == before  # 缓存命中

    @pytest.mark.asyncio
    async def test_list_falls_back_when_no_cache(self, daemon_app):
        client, app, rt, _monitor = daemon_app
        # 把 monitor 干掉模拟还没启动 — 应走 fallback
        app.state.cp.provider_health = None
        rt.push(HealthStatus(available=True, message="live probe"))
        r = client.get("/providers")
        assert r.status_code == 200
        body = r.json()
        assert body["providers"][0]["message"] == "live probe"
        assert rt.probe_calls == 1

    @pytest.mark.asyncio
    async def test_get_provider_404_unknown(self, daemon_app):
        client, _app, _rt, _monitor = daemon_app
        r = client.get("/providers/does-not-exist")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_history_returns_transitions(self, daemon_app):
        client, _app, rt, monitor = daemon_app
        rt.push(HealthStatus(available=True, message="ok"))
        await monitor.probe_one("claude")
        rt.push(HealthStatus(available=False, message="down"))
        await monitor.probe_one("claude")

        r = client.get("/providers/claude/history")
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "claude"
        assert len(body["history"]) == 2
        assert body["history"][1]["to_available"] is False
        assert body["history"][1]["message"] == "down"

    @pytest.mark.asyncio
    async def test_history_unknown_kind_404(self, daemon_app):
        client, _app, _rt, _monitor = daemon_app
        r = client.get("/providers/unknown/history")
        assert r.status_code == 404


# ── HTTP：create_turn 健康门禁 ──────────────────────────────────────


class TestTurnCreationGate:
    @pytest.mark.asyncio
    async def test_unavailable_returns_503(self, daemon_app):
        client, _app, rt, monitor = daemon_app
        # 创建 session
        sr = client.post(
            "/sessions",
            json={"runtime_kind": "claude", "title": "t"},
        )
        assert sr.status_code in (200, 201)
        sid = sr.json()["id"]

        # provider 不可用
        rt.push(HealthStatus(available=False, message="API key missing"))
        await monitor.probe_one("claude")

        r = client.post(
            f"/sessions/{sid}/turns",
            json={"prompt": "hello"},
        )
        assert r.status_code == 503
        body = r.json()
        # 错误信封：detail 里带 code/kind/hint
        detail = body.get("detail") or body.get("error", {}).get("detail") or body
        # 中间件可能套一层 envelope；解开多层兜底
        if isinstance(detail, dict) and "code" in detail:
            assert detail["code"] == "provider_unavailable"
            assert detail["kind"] == "claude"
            assert "hint" in detail
        else:
            # envelope 包了一层 — 至少响应里要能看到 provider_unavailable
            raw = r.text
            assert "provider_unavailable" in raw
            assert "claude" in raw

    @pytest.mark.asyncio
    async def test_health_check_exception_does_not_block(self, daemon_app):
        """探活自身报错时不应拦截 turn 创建（fail-open）。"""
        client, _app, rt, monitor = daemon_app
        sr = client.post(
            "/sessions",
            json={"runtime_kind": "claude", "title": "t"},
        )
        sid = sr.json()["id"]

        rt.push(RuntimeError("probe broke"))
        await monitor.probe_one("claude")
        snap = monitor.snapshot("claude")
        assert snap is not None and snap.error is not None

        # 不该 503 — runtime 没注册 start_turn 实现，会走 no-runtime 分支
        r = client.post(
            f"/sessions/{sid}/turns",
            json={"prompt": "hello"},
        )
        assert r.status_code != 503

    @pytest.mark.asyncio
    async def test_available_passes_through(self, daemon_app):
        client, _app, rt, monitor = daemon_app
        sr = client.post(
            "/sessions",
            json={"runtime_kind": "claude", "title": "t"},
        )
        sid = sr.json()["id"]

        rt.push(HealthStatus(available=True, message="ok"))
        await monitor.probe_one("claude")

        r = client.post(
            f"/sessions/{sid}/turns",
            json={"prompt": "hello"},
        )
        assert r.status_code != 503
