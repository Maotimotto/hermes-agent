"""Wave 8.3 — sessions 路由调用 RuntimeRegistry 测试。

验证：
- 不注册 runtime → 行为不变（创建 DB 记录 + 'created' 状态）
- 注册 runtime → start_session 被调，session.status='active'，
  bind_session 已绑定，provider_session_id 落 metadata
- runtime.start_session 抛错 → DB 记录保留 'created'，不破坏接口
"""

from __future__ import annotations

import os
import tempfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from agent.control_plane.runtimes.interface import (
    AgentRuntime,
    SessionRef,
    StartSessionInput,
)
from gateway.control_plane.app import create_control_plane_app
from gateway.control_plane.deps import AppState


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        yield path
    finally:
        if os.path.exists(path):
            os.unlink(path)


def _make_mock_runtime(provider_session_id: str = "pid_12345") -> MagicMock:
    rt = MagicMock(spec=AgentRuntime)
    rt.start_session = AsyncMock(
        return_value=SessionRef(
            hermes_session_id="",
            provider_session_id=provider_session_id,
            provider="claude",
        )
    )
    return rt


class TestCreateSessionRuntime:
    def test_no_runtime_registered_keeps_created_status(self, db_path):
        """registry 没注册 runtime → session 落 'created'，不调任何 runtime。"""
        app = create_control_plane_app(db_path=db_path, runtime_configs=None)
        with TestClient(app) as client:
            client.get("/health")  # trigger startup
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
            assert r.status_code == 201
            assert r.json()["status"] == "created"

    def test_runtime_registered_starts_and_marks_active(self, db_path):
        """registry 有 runtime → start_session 被调；status='active'，
        provider_session_id 出现在 metadata，session_id 已 bind。"""
        app = create_control_plane_app(db_path=db_path, runtime_configs=None)
        with TestClient(app) as client:
            client.get("/health")

            rt = _make_mock_runtime("provider_sid_xyz")
            state: AppState = app.state.cp
            state.runtime_registry.register("claude", rt)

            r = client.post(
                "/sessions",
                json={
                    "runtime_kind": "claude",
                    "model": "claude-opus-4",
                    "repo_path": "/tmp/repo",
                    "base_branch": "dev",
                    "metadata": {"k": "v"},
                },
            )
            assert r.status_code == 201
            data = r.json()
            assert data["status"] == "running"
            assert data["metadata"]["provider_session_id"] == "provider_sid_xyz"
            assert data["metadata"]["k"] == "v"

            # runtime.start_session 被调 1 次，参数正确
            rt.start_session.assert_awaited_once()
            call = rt.start_session.await_args
            arg = call.args[0]
            assert isinstance(arg, StartSessionInput)
            assert arg.repo_path == "/tmp/repo"
            assert arg.branch == "dev"

            # bind_session 生效
            sid = data["id"]
            assert state.runtime_registry.get_session_runtime(sid) == "claude"

    def test_runtime_failure_keeps_created(self, db_path, caplog):
        """runtime.start_session 抛错 → DB 记录保留 'created'。"""
        app = create_control_plane_app(db_path=db_path, runtime_configs=None)
        with TestClient(app) as client:
            client.get("/health")

            rt = MagicMock(spec=AgentRuntime)
            rt.start_session = AsyncMock(side_effect=RuntimeError("boom"))
            state: AppState = app.state.cp
            state.runtime_registry.register("claude", rt)

            with caplog.at_level("ERROR"):
                r = client.post(
                    "/sessions",
                    json={
                        "runtime_kind": "claude",
                        "model": "m",
                        "repo_path": "/tmp/repo",
                        "metadata": {},
                    },
                )
            assert r.status_code == 201
            assert r.json()["status"] == "created"
            assert any("start_session failed" in rec.getMessage() for rec in caplog.records)

    def test_no_repo_path_skips_runtime(self, db_path):
        """没有 repo_path → 不调 runtime（workspace + start_session 都跳过）。"""
        app = create_control_plane_app(db_path=db_path, runtime_configs=None)
        with TestClient(app) as client:
            client.get("/health")

            rt = _make_mock_runtime()
            state: AppState = app.state.cp
            state.runtime_registry.register("claude", rt)

            r = client.post(
                "/sessions",
                json={
                    "runtime_kind": "claude",
                    "model": "m",
                    "metadata": {},
                },
            )
            assert r.status_code == 201
            assert r.json()["status"] == "created"
            rt.start_session.assert_not_awaited()
