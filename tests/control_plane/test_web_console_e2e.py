"""Wave 10.3/10.4 — Web 控制台 e2e 闭环测试。

覆盖前端 ControlPlanePage 的完整用户流：
1. 创建 session（带 runtime）
2. 发 turn
3. 看到 events
4. 删 session（取消 active turn）

不依赖真 runtime — 用 fake runtime + 注入 factory。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.control_plane.runtimes.interface import (
    AgentRuntime,
    SessionRef,
    StartSessionInput,
    TurnInput,
)
from gateway.control_plane.app import create_control_plane_app
from gateway.control_plane.deps import RuntimeRegistry


class FakeRuntime(AgentRuntime):
    """最小可用 fake runtime — start_session 返回 ref，start_turn 即结束。"""

    def __init__(self):
        self.started_sessions: list[StartSessionInput] = []
        self.started_turns: list[tuple[str, TurnInput]] = []
        self.interrupted: list[str] = []

    @property
    def provider(self):
        return "claude"

    async def start_session(self, input: StartSessionInput) -> SessionRef:
        self.started_sessions.append(input)
        sid = uuid.uuid4().hex[:8]
        return SessionRef(
            hermes_session_id=f"hs_{sid}",
            provider_session_id=f"prov_{sid}",
            provider="claude",
        )

    async def start_turn(self, session_id, input: TurnInput, signal=None):
        self.started_turns.append((session_id, input))
        # 必须是 async generator
        if False:
            yield None
        return

    async def resume_session(self, provider_session_id):
        sid = provider_session_id.split("_")[-1] if "_" in provider_session_id else provider_session_id
        return SessionRef(
            hermes_session_id=f"hs_{sid}",
            provider_session_id=provider_session_id,
            provider="claude",
        )

    async def interrupt_turn(self, turn_id):
        self.interrupted.append(turn_id)
        return True

    async def health(self):
        from agent.control_plane.runtimes.interface import HealthStatus

        return HealthStatus(healthy=True, detail="fake")

    async def health_check(self):
        from agent.control_plane.runtimes.interface import HealthStatus

        return HealthStatus(healthy=True, detail="fake")

    async def resolve_approval(self, approval_id, decision):
        return True

    async def steer_turn(self, turn_id, message):
        return True


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def app_with_fake_runtime(db_path, monkeypatch):
    """注入 fake runtime 工厂。"""
    fake_runtime = FakeRuntime()

    def fake_builder(*, store=None, approval_gate=None, runtime_configs=None, **_):
        return {"claude": fake_runtime}

    monkeypatch.setattr(
        "agent.control_plane.runtimes.factory.build_default_runtimes",
        fake_builder,
    )

    app = create_control_plane_app(
        db_path=db_path,
        runtime_configs={"claude": {"api_key": "test", "model": "test-model"}},
        run_migrations=True,
    )
    app.state._fake_runtime = fake_runtime  # type: ignore[attr-defined]
    return app


class TestWebConsoleE2E:
    """模拟 ControlPlanePage 用户流。"""

    def test_full_loop_create_turn_events_delete(
        self, app_with_fake_runtime, tmp_path
    ):
        # 创建临时 git repo 让 workspace 创建成功
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess

        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "--allow-empty", "-m", "init", "-q"],
            check=True, capture_output=True,
        )

        with TestClient(app_with_fake_runtime) as client:
            fake = app_with_fake_runtime.state._fake_runtime

            # ① 用户点 'New' 填表 → 创建（带 repo_path 才会触发 runtime.start_session）
            r = client.post(
                "/sessions",
                json={
                    "runtime_kind": "claude",
                    "model": "claude-opus-4-7",
                    "repo_path": str(repo),
                },
            )
            assert r.status_code == 201
            sid = r.json()["id"]
            assert r.json()["status"] == "running"
            assert len(fake.started_sessions) == 1
            assert fake.started_sessions[0].repo_path == str(repo)

            # ② 用户在底部输入 prompt → Send（202 Accepted = turn 已入队）
            r = client.post(
                f"/sessions/{sid}/turns",
                json={"prompt": "hello world"},
            )
            assert r.status_code in (201, 202)
            assert r.json()["turn_id"]

            # ③ events 流可查
            r = client.get(f"/sessions/{sid}/events?limit=200")
            assert r.status_code == 200
            assert "events" in r.json()

            # ④ 用户点 ✕ 删 session
            r = client.delete(f"/sessions/{sid}")
            assert r.status_code == 200
            assert r.json()["status"] == "stopped"

            # ⑤ list 中 session 仍可见，但状态 stopped
            r = client.get("/sessions?limit=50")
            ids = {s["id"]: s["status"] for s in r.json()["sessions"]}
            assert ids.get(sid) == "stopped"

    def test_create_without_repo_path_skips_runtime(self, app_with_fake_runtime):
        """没传 repo_path → 不调 runtime（W8.3 现行行为），但 session 仍创建。"""
        with TestClient(app_with_fake_runtime) as client:
            fake = app_with_fake_runtime.state._fake_runtime
            before = len(fake.started_sessions)
            r = client.post(
                "/sessions",
                json={"runtime_kind": "claude", "model": "m"},
            )
            assert r.status_code == 201
            assert r.json()["status"] == "created"
            assert len(fake.started_sessions) == before

    def test_codex_kind_without_runtime_falls_through(self, app_with_fake_runtime):
        """注册了 claude 但请求 codex → 行为退化为 'created' 不报错。"""
        with TestClient(app_with_fake_runtime) as client:
            r = client.post(
                "/sessions",
                json={"runtime_kind": "codex", "model": "gpt-5", "repo_path": "/tmp/x"},
            )
            assert r.status_code == 201
            assert r.json()["status"] == "created"
