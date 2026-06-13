"""Wave 10.2 — Web 控制台后端 API 集成测试。

覆盖 ControlPlanePage 真实使用的 API 路径：
- GET /control-plane/sessions
- GET /control-plane/sessions/:id/events
- GET /control-plane/approvals?status=pending
- POST /control-plane/approvals/:id/decision

只验证形状 + 端到端能跑通，业务逻辑由各自 route 的单测覆盖。
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway.control_plane.app import create_control_plane_app


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def app(db_path):
    return create_control_plane_app(
        db_path=db_path, runtime_configs=None, run_migrations=True
    )


class TestControlPlanePageAPIPaths:
    """前端 ControlPlanePage 实际请求的 4 条路径。"""

    def test_list_sessions_empty(self, app):
        with TestClient(app) as client:
            r = client.get("/sessions?limit=50")
            assert r.status_code == 200
            data = r.json()
            assert "sessions" in data
            assert "limit" in data
            assert "offset" in data
            assert isinstance(data["sessions"], list)

    def test_list_events_404_when_session_missing(self, app):
        with TestClient(app) as client:
            r = client.get(
                f"/sessions/{uuid.uuid4().hex}/events?limit=200"
            )
            assert r.status_code == 404

    def test_list_approvals_pending_empty(self, app):
        with TestClient(app) as client:
            r = client.get("/approvals?status=pending")
            assert r.status_code == 200
            data = r.json()
            # approvals 路由返回裸 list（前端已对齐）
            assert isinstance(data, list)
            assert all(a["decision"] == "pending" for a in data)

    def test_create_session_then_list_events(self, app):
        with TestClient(app) as client:
            # Step 1: 创建一个 session（无 runtime → 行为不变 + status=created）
            create = client.post(
                "/sessions",
                json={
                    "runtime_kind": "claude",
                    "model": "claude-opus-4",
                    "repo_path": "/tmp/repo",
                    "metadata": {},
                },
            )
            assert create.status_code == 201
            sid = create.json()["id"]

            # Step 2: list sessions 应包含新创建的
            lst = client.get("/sessions?limit=50")
            assert lst.status_code == 200
            ids = [s["id"] for s in lst.json()["sessions"]]
            assert sid in ids

            # Step 3: events 应能查（即使为空）
            ev = client.get(f"/sessions/{sid}/events?limit=200")
            assert ev.status_code == 200
            assert "events" in ev.json()

    def test_approval_decision_rejects_unknown_id(self, app):
        with TestClient(app) as client:
            r = client.post(
                f"/approvals/{uuid.uuid4().hex}/decision",
                json={"decision": "approved", "decided_by": "user"},
            )
            assert r.status_code in (404, 400)


class TestApprovalsListShape:
    """确保 list_approvals 永远返回 list（前端依赖这个形状）。"""

    def test_no_filter_returns_list(self, app):
        with TestClient(app) as client:
            r = client.get("/approvals")
            assert r.status_code == 200
            assert isinstance(r.json(), list)

    def test_with_status_filter_returns_list(self, app):
        with TestClient(app) as client:
            for status in ("pending", "approved", "denied"):
                r = client.get(f"/approvals?status={status}")
                assert r.status_code == 200
                data = r.json()
                assert isinstance(data, list)
                if status != "pending":
                    # 即使没有 approved/denied 记录，必须返回空 list 不报错
                    pass
