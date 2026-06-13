"""Wave 7 — CodexAppServerRuntime ↔ ApprovalGate 真接入。

验证：
1. 未传 approval_gate → _build_approval_callback 返回 None（保留底层默认 fail-closed）
2. 传入 gate 后，CodexAppServerSession 拿到 callback
3. callback 桥接逻辑：whitelist → 'once'，blacklist → 'deny'，pending → 用户决议

不启动真 codex 子进程；直接测 _build_approval_callback。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent.control_plane.approval import ApprovalGate
from agent.control_plane.runtimes.codex_app_server_runtime import (
    CodexAppServerRuntime,
)

from tests.control_plane.test_approval_gate import FakeStore, FakeBus


@pytest.fixture
def mock_store():
    from unittest.mock import MagicMock
    return MagicMock()


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def bus() -> FakeBus:
    return FakeBus()


@pytest.fixture
def gate(store: FakeStore, bus: FakeBus) -> ApprovalGate:
    return ApprovalGate(store, bus)


class TestApprovalGateOptional:
    def test_default_none_gate(self, mock_store):
        rt = CodexAppServerRuntime(store=mock_store)
        assert rt._approval_gate is None

    def test_build_callback_returns_none_when_no_gate(self, mock_store):
        rt = CodexAppServerRuntime(store=mock_store)
        cb = rt._build_approval_callback({"id": ""})
        assert cb is None


class TestApprovalCallbackBridge:
    def test_runtime_stores_gate(self, mock_store, gate):
        rt = CodexAppServerRuntime(store=mock_store, approval_gate=gate)
        assert rt._approval_gate is gate

    def test_build_callback_returns_callable(self, mock_store, gate):
        rt = CodexAppServerRuntime(store=mock_store, approval_gate=gate)
        cb = rt._build_approval_callback({"id": "sess_x"})
        assert callable(cb)

    @pytest.mark.asyncio
    async def test_whitelist_returns_once(self, mock_store, gate):
        """whitelist 命中 → callback 返回 'once'。"""
        gate.add_whitelist("ls")
        rt = CodexAppServerRuntime(store=mock_store, approval_gate=gate)
        rt._loops["sess_x"] = asyncio.get_running_loop()
        cb = rt._build_approval_callback({"id": "sess_x"})

        # callback 在 executor 线程跑（不能直接 await）
        result = await asyncio.get_running_loop().run_in_executor(
            None, cb, "ls -la", "list files"
        )
        assert result == "once"

    @pytest.mark.asyncio
    async def test_blacklist_returns_deny(self, mock_store, gate):
        gate.add_blacklist("rm -rf /")
        rt = CodexAppServerRuntime(store=mock_store, approval_gate=gate)
        rt._loops["sess_x"] = asyncio.get_running_loop()
        cb = rt._build_approval_callback({"id": "sess_x"})

        result = await asyncio.get_running_loop().run_in_executor(
            None, cb, "rm -rf /", "danger"
        )
        assert result == "deny"

    @pytest.mark.asyncio
    async def test_no_loop_registered_fail_closed(self, mock_store, gate):
        """approval_callback 在 session 未注册 loop 时 fail-closed。"""
        rt = CodexAppServerRuntime(store=mock_store, approval_gate=gate)
        cb = rt._build_approval_callback({"id": "missing_sess"})
        # 直接调（同步），不需要 executor
        result = cb("ls", "desc")
        assert result == "deny"

    @pytest.mark.asyncio
    async def test_pending_then_user_approved(self, mock_store, gate):
        """policy 命中 pending → 用户 approve → callback 返回 'once'。"""
        rt = CodexAppServerRuntime(store=mock_store, approval_gate=gate)
        loop = asyncio.get_running_loop()
        rt._loops["sess_x"] = loop
        cb = rt._build_approval_callback({"id": "sess_x"})

        # 在 executor 跑 callback（它会阻塞等用户决议）
        fut = loop.run_in_executor(None, cb, "deploy.sh", "deploy production")
        # 给 ApprovalGate.evaluate 时间登记
        await asyncio.sleep(0.05)
        pending = [r for r in gate._store.records.values() if r.decision == "pending"]
        assert len(pending) == 1
        await gate.resolve(pending[0].id, "approved", decided_by="user")
        result = await asyncio.wait_for(fut, timeout=1.0)
        assert result == "once"

    @pytest.mark.asyncio
    async def test_pending_then_user_denied(self, mock_store, gate):
        rt = CodexAppServerRuntime(store=mock_store, approval_gate=gate)
        loop = asyncio.get_running_loop()
        rt._loops["sess_x"] = loop
        cb = rt._build_approval_callback({"id": "sess_x"})

        fut = loop.run_in_executor(None, cb, "deploy.sh", "deploy production")
        await asyncio.sleep(0.05)
        pending = [r for r in gate._store.records.values() if r.decision == "pending"]
        assert len(pending) == 1
        await gate.resolve(pending[0].id, "denied", decided_by="user")
        result = await asyncio.wait_for(fut, timeout=1.0)
        assert result == "deny"
