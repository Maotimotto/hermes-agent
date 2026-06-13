"""Wave 7 — Runtime ↔ ApprovalGate 真接入。

验证 ClaudeAgentSdkRuntime._build_can_use_tool 把 SDK can_use_tool 回调正确
连到 ApprovalGate.evaluate / request_tool_approval。

不依赖真 SDK 子进程；直接调用 _build_can_use_tool 返回的 coroutine。
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.control_plane.approval import ApprovalGate
from agent.control_plane.runtimes.claude_agent_sdk_runtime import (
    ClaudeAgentSdkRuntime,
)

# 复用 test_approval_gate 里的 FakeStore / FakeBus（同包内）
from tests.control_plane.test_approval_gate import FakeStore, FakeBus


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def claude_config() -> dict[str, Any]:
    return {
        "api_key": "sk-test-123",
        "model": "claude-opus-4",
        "base_url": "https://api.anthropic.com",
    }


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def bus() -> FakeBus:
    return FakeBus()


@pytest.fixture
def gate(store: FakeStore, bus: FakeBus) -> ApprovalGate:
    return ApprovalGate(store, bus)


# ── 默认无 gate（向后兼容）───────────────────────────────────────────────────


class TestApprovalGateOptional:
    def test_default_none_gate(self, claude_config):
        rt = ClaudeAgentSdkRuntime(config=claude_config)
        assert rt._approval_gate is None

    def test_build_can_use_tool_returns_none_when_no_gate(self, claude_config):
        rt = ClaudeAgentSdkRuntime(config=claude_config)
        cb = rt._build_can_use_tool("sess", "turn")
        assert cb is None  # SDK 视 None 为"不拦截"


# ── 真正接入 ApprovalGate ────────────────────────────────────────────────────


class TestCanUseToolWiring:
    def test_runtime_stores_gate(self, claude_config, gate):
        rt = ClaudeAgentSdkRuntime(config=claude_config, approval_gate=gate)
        assert rt._approval_gate is gate

    def test_build_can_use_tool_returns_callable(self, claude_config, gate):
        rt = ClaudeAgentSdkRuntime(config=claude_config, approval_gate=gate)
        cb = rt._build_can_use_tool("sess_x", "turn_y")
        assert cb is not None
        assert callable(cb)

    @pytest.mark.asyncio
    async def test_whitelist_returns_allow(self, claude_config, gate):
        from claude_agent_sdk import PermissionResultAllow

        gate.add_whitelist("ls")
        rt = ClaudeAgentSdkRuntime(config=claude_config, approval_gate=gate)
        cb = rt._build_can_use_tool("sess_x", "turn_y")
        result = await cb("Bash", {"command": "ls -la"}, ctx=None)
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_blacklist_returns_deny(self, claude_config, gate, store):
        from claude_agent_sdk import PermissionResultDeny

        gate.add_blacklist("rm -rf /")
        rt = ClaudeAgentSdkRuntime(config=claude_config, approval_gate=gate)
        cb = rt._build_can_use_tool("sess_x", "turn_y")
        result = await cb("Bash", {"command": "rm -rf /"}, ctx=None)
        assert isinstance(result, PermissionResultDeny)
        assert "denied" in (result.message or "").lower()

    @pytest.mark.asyncio
    async def test_remembered_approval_returns_allow(
        self, claude_config, gate, store
    ):
        from claude_agent_sdk import PermissionResultAllow
        from agent.control_plane.store import ApprovalRecord
        from datetime import datetime, timezone

        # 注入一条"已记忆=approved"决策
        store.remembered = ApprovalRecord(
            id="rem_1",
            session_id="s",
            action_kind="file_write",
            action_payload={},
            risk="medium",
            decision="approved",
            turn_id="t",
            decided_at=datetime.now(timezone.utc).isoformat(),
            decided_by="user",
            ttl_until=None,
        )
        rt = ClaudeAgentSdkRuntime(config=claude_config, approval_gate=gate)
        cb = rt._build_can_use_tool("sess_x", "turn_y")
        result = await cb("Write", {"path": "/tmp/foo"}, ctx=None)
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_pending_then_user_approved(self, claude_config, gate):
        """policy 落到 pending → 用户决议 approved → SDK 收到 Allow。"""
        import asyncio
        from claude_agent_sdk import PermissionResultAllow

        rt = ClaudeAgentSdkRuntime(config=claude_config, approval_gate=gate)
        cb = rt._build_can_use_tool("sess_x", "turn_y")

        # 在另一个 task 里发起请求（会 await 用户决议）
        task = asyncio.create_task(
            cb("Bash", {"command": "deploy.sh"}, ctx=None)
        )
        # 给 gate.evaluate 一些时间登记 pending
        await asyncio.sleep(0.05)
        # 找到 pending 的 approval_id
        pending = [r for r in gate._store.records.values() if r.decision == "pending"]
        assert len(pending) == 1
        from agent.control_plane.approval.types import ApprovalDecision

        await gate.resolve(
            pending[0].id,
            "approved",
            decided_by="user",
        )
        result = await asyncio.wait_for(task, timeout=1.0)
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_pending_then_user_denied(self, claude_config, gate):
        import asyncio
        from claude_agent_sdk import PermissionResultDeny

        rt = ClaudeAgentSdkRuntime(config=claude_config, approval_gate=gate)
        cb = rt._build_can_use_tool("sess_x", "turn_y")

        task = asyncio.create_task(
            cb("Bash", {"command": "deploy.sh"}, ctx=None)
        )
        await asyncio.sleep(0.05)
        pending = [r for r in gate._store.records.values() if r.decision == "pending"]
        assert len(pending) == 1
        from agent.control_plane.approval.types import ApprovalDecision

        await gate.resolve(
            pending[0].id,
            "denied",
            decided_by="user",
        )
        result = await asyncio.wait_for(task, timeout=1.0)
        assert isinstance(result, PermissionResultDeny)
