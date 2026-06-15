"""Provider 路由 + 错误中间件的端到端测试。

复用 ``tests/control_plane/test_daemon_api`` 里的 MockRuntime，避免重复造抽象方法。
为「health_check 抛异常」场景额外加一个 FlakyRuntime（继承 MockRuntime，覆盖 health_check）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.control_plane.runtimes.interface import HealthStatus
from gateway.control_plane.app import create_control_plane_app, init_store
from gateway.control_plane.deps import AppState

from tests.control_plane.test_daemon_api import MockRuntime


class FlakyRuntime(MockRuntime):
    """health_check 直接抛异常，验证 _probe 是否能兜住。"""

    async def health_check(self) -> HealthStatus:  # type: ignore[override]
        raise RuntimeError("provider exploded on health probe")


class TunedHealthRuntime(MockRuntime):
    """覆盖 health_check 让我们能断言版本/延迟字段透传。"""

    async def health_check(self) -> HealthStatus:  # type: ignore[override]
        return HealthStatus(available=True, message="ok", version="1.0.0", latency=12)


@pytest.fixture()
def app_with_runtimes(tmp_path: Path) -> tuple[FastAPI, FastAPI]:
    """两个 runtime：claude 健康，codex health_check 抛异常。"""
    db_path = tmp_path / "providers_test.db"
    cp = create_control_plane_app(db_path=str(db_path), run_migrations=True)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(init_store(cp))
    loop.close()

    state: AppState = cp.state.cp  # type: ignore[assignment]
    state.runtime_registry.register("claude", TunedHealthRuntime())
    state.runtime_registry.register("codex", FlakyRuntime())

    main_app = FastAPI()
    main_app.mount("/control-plane", cp)
    main_app.state.cp_app = cp  # type: ignore[attr-defined]
    return main_app, cp


@pytest.fixture()
def client(app_with_runtimes: tuple[FastAPI, FastAPI]) -> Iterator[TestClient]:
    main_app, _ = app_with_runtimes
    with TestClient(main_app, raise_server_exceptions=False) as c:
        yield c


# ─── /providers 列表 ────────────────────────────────────────


class TestProviderList:
    def test_list_providers_returns_both(self, client: TestClient) -> None:
        resp = client.get("/control-plane/providers")
        assert resp.status_code == 200
        body = resp.json()
        kinds = [p["kind"] for p in body["providers"]]
        assert kinds == ["claude", "codex"]  # 字典序

    def test_good_runtime_is_available(self, client: TestClient) -> None:
        resp = client.get("/control-plane/providers")
        body = resp.json()
        claude = next(p for p in body["providers"] if p["kind"] == "claude")
        assert claude["available"] is True
        assert claude["version"] == "1.0.0"
        assert claude["latency_ms"] == 12

    def test_flaky_runtime_marked_unavailable_not_500(
        self, client: TestClient
    ) -> None:
        """health_check 抛异常时，列表 endpoint 仍应返回 200，
        把该 provider 标 available=False，而不是把整个列表炸成 500。"""
        resp = client.get("/control-plane/providers")
        assert resp.status_code == 200
        body = resp.json()
        codex = next(p for p in body["providers"] if p["kind"] == "codex")
        assert codex["available"] is False
        assert "exploded" in codex["message"]

    def test_active_sessions_count(
        self, client: TestClient, app_with_runtimes: tuple[FastAPI, FastAPI]
    ) -> None:
        """绑定一些 session 到 claude，检查计数。"""
        _, cp = app_with_runtimes
        state: AppState = cp.state.cp  # type: ignore[assignment]
        state.runtime_registry.bind_session("s1", "claude")
        state.runtime_registry.bind_session("s2", "claude")
        state.runtime_registry.bind_session("s3", "codex")

        body = client.get("/control-plane/providers").json()
        claude = next(p for p in body["providers"] if p["kind"] == "claude")
        codex = next(p for p in body["providers"] if p["kind"] == "codex")
        assert claude["active_sessions"] == 2
        assert codex["active_sessions"] == 1


# ─── /providers/{kind} 单查 ──────────────────────────────────


class TestProviderGet:
    def test_get_known_provider(self, client: TestClient) -> None:
        resp = client.get("/control-plane/providers/claude")
        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "claude"
        assert body["available"] is True

    def test_get_unknown_provider_returns_404_envelope(
        self, client: TestClient
    ) -> None:
        """未注册 kind 走错误中间件，应返回标准 ErrorResponse 信封。"""
        resp = client.get("/control-plane/providers/gemini")
        assert resp.status_code == 404
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "not_found"
        assert "gemini" in body["error"]["message"]
        assert body["error"]["request_id"]


# ─── 错误中间件 ──────────────────────────────────────────────


class TestErrorMiddleware:
    def test_404_envelope_shape(self, client: TestClient) -> None:
        resp = client.get("/control-plane/sessions/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert "error" in body
        err = body["error"]
        assert err["code"] == "not_found"
        assert err["message"]
        assert err["request_id"]
        assert len(err["request_id"]) == 12  # uuid4 hex 截 12

    def test_validation_error_envelope_shape(self, client: TestClient) -> None:
        """POST 一个非法 runtime_kind 触发 422，验证走 ErrorResponse 信封。"""
        resp = client.post(
            "/control-plane/sessions",
            json={"runtime_kind": "not_a_real_kind"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert "error" in body
        err = body["error"]
        assert err["code"] == "validation_error"
        assert err["message"] == "request validation failed"
        assert isinstance(err["details"], list)
        assert err["request_id"]

    def test_request_ids_are_unique(self, client: TestClient) -> None:
        """两次错误请求的 request_id 不应该重复，便于关联日志。"""
        rid1 = client.get("/control-plane/sessions/x1").json()["error"][
            "request_id"
        ]
        rid2 = client.get("/control-plane/sessions/x2").json()["error"][
            "request_id"
        ]
        assert rid1 != rid2
