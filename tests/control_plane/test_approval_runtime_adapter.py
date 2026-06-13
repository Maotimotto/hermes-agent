"""Tests for the runtime adapter (request_tool_approval + helpers)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent.control_plane.approval import (
    ApprovalGate,
    infer_action_kind,
    needs_approval,
    request_tool_approval,
)
from agent.control_plane.store import ApprovalRecord


class FakeStore:
    def __init__(self) -> None:
        self.records: dict[str, ApprovalRecord] = {}

    async def record_request(self, approval: ApprovalRecord) -> None:
        self.records[approval.id] = approval

    async def record_decision(
        self, approval_id: str, decision: str, **kwargs: Any
    ) -> None:
        rec = self.records[approval_id]
        rec.decision = decision
        rec.decided_by = kwargs.get("decided_by")
        rec.ttl_until = kwargs.get("ttl_until")

    async def find_remembered_decision(self, action_kind: str, fingerprint: str | None = None):
        return None

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        return self.records.get(approval_id)


class FakeBus:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def publish(self, session_id: str, event: dict[str, Any]) -> None:
        self.events.append(event)


@pytest.fixture()
def gate() -> ApprovalGate:
    return ApprovalGate(FakeStore(), FakeBus())


# ── infer_action_kind ──────────────────────────────────────────────────────


class TestInferActionKind:
    @pytest.mark.parametrize("name", ["Bash", "bash", "shell", "exec", "run_shell_cmd"])
    def test_shell(self, name: str) -> None:
        assert infer_action_kind(name, {"command": "ls"}) == "shell"

    @pytest.mark.parametrize("name", ["Write", "Edit", "patch", "write_file"])
    def test_file(self, name: str) -> None:
        assert infer_action_kind(name, {"path": "/tmp/x"}) == "file_write"

    def test_other(self) -> None:
        assert infer_action_kind("WebSearch", {"q": "x"}) == "tool"


class TestNeedsApproval:
    def test_yes_for_shell_and_file(self) -> None:
        assert needs_approval("Bash") is True
        assert needs_approval("Write") is True

    def test_no_for_other(self) -> None:
        assert needs_approval("WebSearch") is False
        assert needs_approval("ReadFile") is False


# ── request_tool_approval end-to-end ───────────────────────────────────────


class TestRequestToolApproval:
    @pytest.mark.asyncio
    async def test_whitelist_returns_allowed_no_wait(self, gate: ApprovalGate) -> None:
        gate.add_whitelist("ls")
        allowed, source = await request_tool_approval(
            gate,
            session_id="s1",
            turn_id="t1",
            tool_name="Bash",
            tool_input={"command": "ls -la"},
        )
        assert allowed is True
        assert source == "whitelist"

    @pytest.mark.asyncio
    async def test_blacklist_returns_denied(self, gate: ApprovalGate) -> None:
        gate.add_blacklist("dangerous_cmd")
        allowed, source = await request_tool_approval(
            gate,
            session_id="s1",
            turn_id="t1",
            tool_name="Bash",
            tool_input={"command": "dangerous_cmd"},
        )
        assert allowed is False
        assert source == "blacklist"

    @pytest.mark.asyncio
    async def test_pending_waits_for_resolver(self, gate: ApprovalGate) -> None:
        async def call() -> tuple[bool, str]:
            return await request_tool_approval(
                gate,
                session_id="s1",
                turn_id="t1",
                tool_name="Bash",
                tool_input={"command": "rm -rf /tmp/x"},
            )

        async def resolver() -> None:
            # Poll the gate's in-memory pending map until the request lands,
            # then approve it.
            for _ in range(50):
                if gate._pending:
                    approval_id = next(iter(gate._pending))
                    await gate.resolve(approval_id, "approved")
                    return
                await asyncio.sleep(0.01)
            raise RuntimeError("no pending approval observed")

        results = await asyncio.gather(call(), resolver())
        allowed, source = results[0]
        assert allowed is True
        assert source == "user"

    @pytest.mark.asyncio
    async def test_pending_denied_returns_false(self, gate: ApprovalGate) -> None:
        async def call() -> tuple[bool, str]:
            return await request_tool_approval(
                gate,
                session_id="s1",
                turn_id="t1",
                tool_name="Write",
                tool_input={"path": "/tmp/x"},
            )

        async def resolver() -> None:
            for _ in range(50):
                if gate._pending:
                    aid = next(iter(gate._pending))
                    await gate.resolve(aid, "denied")
                    return
                await asyncio.sleep(0.01)

        results = await asyncio.gather(call(), resolver())
        allowed, _ = results[0]
        assert allowed is False
