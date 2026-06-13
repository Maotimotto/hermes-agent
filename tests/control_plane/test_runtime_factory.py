"""Wave 8.1 — RuntimeFactory 测试。

不依赖真 SDK / codex 子进程；通过 overrides 注入 mock builder。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from agent.control_plane.approval import ApprovalGate
from agent.control_plane.runtimes.factory import (
    build_default_runtimes,
    build_runtime,
)
from agent.control_plane.runtimes.interface import AgentRuntime

from tests.control_plane.test_approval_gate import FakeStore, FakeBus


# ── 一个超薄 mock runtime ─────────────────────────────────────────────────────


class _MockRuntime:
    """挂个签名记录器，验证工厂传参。"""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    @property
    def provider(self) -> str:
        return "mock"

    async def start_session(self, *a, **kw): raise NotImplementedError
    async def start_turn(self, *a, **kw): raise NotImplementedError
    async def resume_session(self, *a, **kw): raise NotImplementedError
    async def steer_turn(self, *a, **kw): raise NotImplementedError
    async def interrupt_turn(self, *a, **kw): raise NotImplementedError
    async def resolve_approval(self, *a, **kw): raise NotImplementedError
    async def health_check(self, *a, **kw): raise NotImplementedError


# 让 _MockRuntime 通过 isinstance(AgentRuntime) 检查（不靠 ABC，靠 register）
AgentRuntime.register(_MockRuntime)  # type: ignore[arg-type]


@pytest.fixture
def store():
    return MagicMock()


@pytest.fixture
def gate():
    return ApprovalGate(FakeStore(), FakeBus())


# ── build_runtime 单测 ───────────────────────────────────────────────────────


class TestBuildRuntime:
    def test_unknown_kind_raises(self, store):
        with pytest.raises(ValueError, match="unknown runtime kind"):
            build_runtime(
                "nope",
                store=store,
                approval_gate=None,
                config={},
            )

    def test_codex_default_builder(self, store):
        """默认 builder 实例化 CodexAppServerRuntime（不传 gate）。"""
        from agent.control_plane.runtimes.codex_app_server_runtime import (
            CodexAppServerRuntime,
        )

        rt = build_runtime(
            "codex",
            store=store,
            approval_gate=None,
            config={"codex_bin": "/usr/bin/codex"},
        )
        assert isinstance(rt, CodexAppServerRuntime)
        assert rt._approval_gate is None
        assert rt._codex_bin == "/usr/bin/codex"

    def test_codex_with_gate(self, store, gate):
        from agent.control_plane.runtimes.codex_app_server_runtime import (
            CodexAppServerRuntime,
        )

        rt = build_runtime(
            "codex",
            store=store,
            approval_gate=gate,
            config={},
        )
        assert isinstance(rt, CodexAppServerRuntime)
        assert rt._approval_gate is gate

    def test_claude_default_builder(self, store):
        from agent.control_plane.runtimes.claude_agent_sdk_runtime import (
            ClaudeAgentSdkRuntime,
        )

        rt = build_runtime(
            "claude",
            store=store,
            approval_gate=None,
            config={
                "api_key": "sk-test",
                "model": "claude-opus-4",
                "base_url": "https://example.com",
            },
        )
        assert isinstance(rt, ClaudeAgentSdkRuntime)
        assert rt._approval_gate is None
        assert rt._config["api_key"] == "sk-test"

    def test_claude_with_gate(self, store, gate):
        from agent.control_plane.runtimes.claude_agent_sdk_runtime import (
            ClaudeAgentSdkRuntime,
        )

        rt = build_runtime(
            "claude",
            store=store,
            approval_gate=gate,
            config={"api_key": "sk", "model": "m"},
        )
        assert isinstance(rt, ClaudeAgentSdkRuntime)
        assert rt._approval_gate is gate

    def test_overrides_replaces_builder(self, store, gate):
        """通过 overrides 注入 mock builder 验证调用链。"""

        captured: dict[str, Any] = {}

        def mock_builder(*, store, approval_gate, config):
            captured["store"] = store
            captured["gate"] = approval_gate
            captured["config"] = config
            return _MockRuntime(
                store=store, gate=approval_gate, config=config
            )

        rt = build_runtime(
            "codex",
            store=store,
            approval_gate=gate,
            config={"x": 1},
            overrides={"codex": mock_builder},
        )
        assert isinstance(rt, _MockRuntime)
        assert captured["store"] is store
        assert captured["gate"] is gate
        assert captured["config"] == {"x": 1}


# ── build_default_runtimes 集成 ─────────────────────────────────────────────


class TestBuildDefaultRuntimes:
    def test_builds_both(self, store, gate):
        rts = build_default_runtimes(
            store=store,
            approval_gate=gate,
            runtime_configs={
                "codex": {},
                "claude": {"api_key": "sk", "model": "m"},
            },
        )
        assert set(rts.keys()) == {"codex", "claude"}
        assert all(getattr(rt, "_approval_gate") is gate for rt in rts.values())

    def test_partial_configs(self, store, gate):
        """只传 claude → 只构造 claude。"""
        rts = build_default_runtimes(
            store=store,
            approval_gate=gate,
            runtime_configs={"claude": {"api_key": "sk", "model": "m"}},
        )
        assert set(rts.keys()) == {"claude"}

    def test_skip_failing_kind(self, store, gate, caplog):
        """某 kind 构造失败不影响其他 kind。"""

        def boom(*, store, approval_gate, config):
            raise RuntimeError("boom")

        with caplog.at_level("ERROR"):
            rts = build_default_runtimes(
                store=store,
                approval_gate=gate,
                runtime_configs={
                    "codex": {},
                    "claude": {"api_key": "sk", "model": "m"},
                },
                overrides={"codex": boom},
            )
        assert "codex" not in rts
        assert "claude" in rts
        assert any("kind=codex" in rec.getMessage() for rec in caplog.records)

    def test_no_gate_passes_none(self, store):
        rts = build_default_runtimes(
            store=store,
            approval_gate=None,
            runtime_configs={"codex": {}},
        )
        assert rts["codex"]._approval_gate is None
