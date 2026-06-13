"""Tests for ApprovalGate (whitelist/blacklist/remembered/pending/resolve)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from agent.control_plane.approval import (
    ApprovalGate,
    ApprovalRequest,
    RiskLevel,
    fingerprint,
)
from agent.control_plane.store import ApprovalRecord


# ── In-memory test doubles ──────────────────────────────────────────────────


class FakeStore:
    """In-memory drop-in for SessionStore.approvals operations."""

    def __init__(self) -> None:
        self.records: dict[str, ApprovalRecord] = {}
        self.remembered: ApprovalRecord | None = None

    async def record_request(self, approval: ApprovalRecord) -> None:
        # Mirror real semantics: first write wins (PK collision raises).
        self.records[approval.id] = approval

    async def record_decision(
        self,
        approval_id: str,
        decision: str,
        *,
        decided_by: str | None = None,
        ttl_until: str | None = None,
    ) -> None:
        rec = self.records.get(approval_id)
        if rec is None:
            raise KeyError(approval_id)
        rec.decision = decision
        rec.decided_by = decided_by
        rec.decided_at = datetime.now(timezone.utc).isoformat()
        rec.ttl_until = ttl_until

    async def find_remembered_decision(
        self,
        action_kind: str,
        fingerprint: str | None = None,
    ) -> ApprovalRecord | None:
        if self.remembered and self.remembered.action_kind == action_kind:
            return self.remembered
        return None

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        return self.records.get(approval_id)


class FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def publish(self, session_id: str, event: dict[str, Any]) -> None:
        self.events.append((session_id, event))


@pytest.fixture()
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture()
def bus() -> FakeBus:
    return FakeBus()


@pytest.fixture()
def gate(store: FakeStore, bus: FakeBus) -> ApprovalGate:
    return ApprovalGate(store, bus)


def _req(kind: str = "shell", **payload: Any) -> ApprovalRequest:
    return ApprovalRequest(
        action_kind=kind,
        action_payload=payload or None,
        session_id="sess_test",
        turn_id="turn_test",
    )


# ── Whitelist / Blacklist ───────────────────────────────────────────────────


class TestWhitelist:
    @pytest.mark.asyncio
    async def test_whitelist_auto_approves(
        self, gate: ApprovalGate, store: FakeStore, bus: FakeBus
    ) -> None:
        gate.add_whitelist("ls")
        res = await gate.evaluate(_req(command="ls -la"))
        assert res.decision == "approved"
        assert res.source == "whitelist"
        assert res.awaitable is None
        # Persisted with decided_by=whitelist
        rec = store.records[res.approval_id]
        assert rec.decision == "approved"
        assert rec.decided_by == "whitelist"
        # No approval.requested event emitted
        assert not any(e[1]["type"] == "approval.requested" for e in bus.events)

    @pytest.mark.asyncio
    async def test_whitelist_does_not_match(
        self, gate: ApprovalGate, store: FakeStore
    ) -> None:
        gate.add_whitelist("ls")
        # Different command — whitelist must not catch it
        res = await gate.evaluate(_req(command="cat /tmp/foo"))
        assert res.decision == "pending"


class TestBlacklist:
    @pytest.mark.asyncio
    async def test_blacklist_auto_denies(
        self, gate: ApprovalGate, bus: FakeBus
    ) -> None:
        gate.add_blacklist("dangerous_cmd")
        res = await gate.evaluate(_req(command="dangerous_cmd --now"))
        assert res.decision == "denied"
        assert res.source == "blacklist"
        assert res.awaitable is None
        assert not any(e[1]["type"] == "approval.requested" for e in bus.events)


# ── Remembered decision ─────────────────────────────────────────────────────


class TestRemembered:
    @pytest.mark.asyncio
    async def test_remembered_approved_reuse(
        self, gate: ApprovalGate, store: FakeStore
    ) -> None:
        store.remembered = ApprovalRecord(
            id="apr_prev",
            session_id="sess_test",
            action_kind="shell",
            decision="approved",
            ttl_until=(
                datetime.now(timezone.utc) + timedelta(seconds=3600)
            ).isoformat(),
        )
        res = await gate.evaluate(_req(command="ls"))
        assert res.decision == "approved"
        assert res.source == "remembered"


# ── Pending + resolve ───────────────────────────────────────────────────────


class TestPendingResolve:
    @pytest.mark.asyncio
    async def test_pending_emits_event_and_returns_future(
        self, gate: ApprovalGate, bus: FakeBus, store: FakeStore
    ) -> None:
        res = await gate.evaluate(_req(command="rm -rf /tmp/important"))
        assert res.decision == "pending"
        assert res.awaitable is not None
        assert not res.awaitable.done()
        # Event emitted
        evs = [e[1] for e in bus.events if e[1]["type"] == "approval.requested"]
        assert len(evs) == 1
        assert evs[0]["approval_id"] == res.approval_id
        assert evs[0]["risk"] == "critical"
        # Persisted as pending
        rec = store.records[res.approval_id]
        assert rec.decision == "pending"
        assert rec.risk == "critical"

    @pytest.mark.asyncio
    async def test_resolve_approved_wakes_future(
        self, gate: ApprovalGate, bus: FakeBus
    ) -> None:
        res = await gate.evaluate(_req(command="rm -rf /tmp/x"))
        assert res.awaitable is not None

        # Resolve from another task
        async def resolver() -> None:
            await asyncio.sleep(0.01)
            await gate.resolve(res.approval_id, "approved", decided_by="user_test")

        await asyncio.gather(resolver(), asyncio.wait_for(res.awaitable, timeout=1.0))
        assert res.awaitable.result() == "approved"

        # approval.resolved event emitted
        resolved = [e[1] for e in bus.events if e[1]["type"] == "approval.resolved"]
        assert len(resolved) == 1
        assert resolved[0]["decision"] == "approved"
        assert resolved[0]["decided_by"] == "user_test"

    @pytest.mark.asyncio
    async def test_resolve_denied(
        self, gate: ApprovalGate
    ) -> None:
        res = await gate.evaluate(_req(command="rm -rf /tmp/x"))
        assert res.awaitable is not None

        await gate.resolve(res.approval_id, "denied")
        assert res.awaitable.result() == "denied"

    @pytest.mark.asyncio
    async def test_resolve_with_ttl_sets_ttl_until(
        self, gate: ApprovalGate, store: FakeStore
    ) -> None:
        res = await gate.evaluate(_req(command="rm -rf /tmp/x"))
        await gate.resolve(res.approval_id, "approved", ttl_seconds=3600)
        rec = store.records[res.approval_id]
        assert rec.ttl_until is not None
        # Roughly 1 hour from now
        until = datetime.fromisoformat(rec.ttl_until)
        delta = until - datetime.now(timezone.utc)
        assert timedelta(seconds=3500) < delta < timedelta(seconds=3700)

    @pytest.mark.asyncio
    async def test_resolve_invalid_decision_raises(
        self, gate: ApprovalGate
    ) -> None:
        res = await gate.evaluate(_req(command="rm -rf /tmp/x"))
        with pytest.raises(ValueError):
            await gate.resolve(res.approval_id, "maybe")


# ── Fingerprint ─────────────────────────────────────────────────────────────


class TestFingerprint:
    def test_same_command_same_fingerprint(self) -> None:
        a = _req(command="ls -la")
        b = _req(command="ls   -la")  # extra whitespace
        assert fingerprint(a) == fingerprint(b)

    def test_different_command_different_fingerprint(self) -> None:
        a = _req(command="ls -la")
        b = _req(command="ls /etc")
        assert fingerprint(a) != fingerprint(b)
