"""
Tests for Session Store (SQLite storage layer).

Covers:
- Migration idempotency
- Session CRUD round-trip
- Event append + batch write + list
- Approval record + find_remembered_decision
- Batch write performance (100 events < 1s)
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from agent.control_plane.store import (
    SessionStore,
    SessionRecord,
    EventRecord,
    ApprovalRecord,
)


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    """Create a fresh SessionStore with a temp DB for each test."""
    db_path = tmp_path / "test.db"
    s = SessionStore(db_path=db_path)
    await s.init()
    yield s
    await s.close()


# ── Migration tests ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_migration_runs_cleanly(tmp_path: Path):
    """Migrations should apply without errors on a fresh DB."""
    db_path = tmp_path / "migrate_test.db"
    store = SessionStore(db_path=db_path)
    await store.init()
    # Verify tables exist by querying them
    cursor = await store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    rows = await cursor.fetchall()
    table_names = {r[0] for r in rows}
    assert "sessions" in table_names
    assert "turns" in table_names
    assert "events" in table_names
    assert "approvals" in table_names
    assert "_migrations" in table_names
    await store.close()


@pytest.mark.asyncio
async def test_migration_idempotent(tmp_path: Path):
    """Running migrations twice should not fail (idempotent)."""
    db_path = tmp_path / "idempotent_test.db"
    store = SessionStore(db_path=db_path)
    await store.init()
    # Run migrations again — should be a no-op
    from agent.control_plane.store.migrate import run_migrations
    await run_migrations(store.db)
    # Check only one migration version recorded
    cursor = await store.db.execute("SELECT COUNT(*) FROM _migrations")
    count = (await cursor.fetchone())[0]
    assert count == 1
    await store.close()


@pytest.mark.asyncio
async def test_migration_version_tracked(tmp_path: Path):
    """The _migrations table should record applied versions."""
    db_path = tmp_path / "version_test.db"
    store = SessionStore(db_path=db_path)
    await store.init()
    cursor = await store.db.execute("SELECT version FROM _migrations")
    rows = await cursor.fetchall()
    versions = [r[0] for r in rows]
    assert "001_init" in versions
    await store.close()


# ── Session CRUD tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_get_session(store: SessionStore):
    """create_session + get_session round-trip."""
    session = SessionRecord(
        id="sess-001",
        runtime_kind="claude",
        model="claude-sonnet-4-20250514",
        repo_path="/home/user/project",
        workspace_id="ws-001",
        status="created",
    )
    await store.create_session(session)

    fetched = await store.get_session("sess-001")
    assert fetched is not None
    assert fetched.id == "sess-001"
    assert fetched.runtime_kind == "claude"
    assert fetched.model == "claude-sonnet-4-20250514"
    assert fetched.repo_path == "/home/user/project"
    assert fetched.status == "created"


@pytest.mark.asyncio
async def test_get_session_not_found(store: SessionStore):
    """get_session returns None for missing IDs."""
    result = await store.get_session("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_list_sessions(store: SessionStore):
    """list_sessions returns sessions ordered by started_at desc."""
    for i in range(3):
        await store.create_session(SessionRecord(
            id=f"sess-list-{i}",
            status="running" if i == 0 else "created",
        ))

    all_sessions = await store.list_sessions()
    assert len(all_sessions) == 3

    running = await store.list_sessions(status="running")
    assert len(running) == 1
    assert running[0].id == "sess-list-0"


@pytest.mark.asyncio
async def test_update_session_status(store: SessionStore):
    """update_session_status changes status and sets ended_at."""
    await store.create_session(SessionRecord(id="sess-update"))

    await store.update_session_status("sess-update", "completed")
    fetched = await store.get_session("sess-update")
    assert fetched is not None
    assert fetched.status == "completed"
    assert fetched.ended_at is not None


@pytest.mark.asyncio
async def test_session_with_metadata(store: SessionStore):
    """Metadata dict survives round-trip through JSON serialization."""
    meta = {"tags": ["test", "round-trip"], "nested": {"key": "value"}}
    await store.create_session(SessionRecord(
        id="sess-meta",
        metadata=meta,
    ))
    fetched = await store.get_session("sess-meta")
    assert fetched is not None
    assert fetched.metadata == meta


# ── Event tests ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_append_event_and_list(store: SessionStore):
    """Single event append + list_events round-trip."""
    await store.create_session(SessionRecord(id="sess-evt"))

    event = EventRecord(
        session_id="sess-evt",
        type="assistant.message",
        payload={"text": "hello world"},
    )
    await store.append_event(event)

    events = await store.list_events("sess-evt")
    assert len(events) == 1
    assert events[0].type == "assistant.message"
    assert events[0].payload["text"] == "hello world"


@pytest.mark.asyncio
async def test_list_events_with_type_filter(store: SessionStore):
    """list_events with types filter returns only matching events."""
    await store.create_session(SessionRecord(id="sess-filter"))
    await store.append_events_batch([
        EventRecord(session_id="sess-filter", type="assistant.message", payload={"t": 1}),
        EventRecord(session_id="sess-filter", type="tool.started", payload={"t": 2}),
        EventRecord(session_id="sess-filter", type="assistant.message", payload={"t": 3}),
    ])

    msgs = await store.list_events("sess-filter", types=["assistant.message"])
    assert len(msgs) == 2
    assert all(e.type == "assistant.message" for e in msgs)


@pytest.mark.asyncio
async def test_list_events_since_id(store: SessionStore):
    """list_events with since_id returns only newer events."""
    await store.create_session(SessionRecord(id="sess-since"))
    await store.append_events_batch([
        EventRecord(session_id="sess-since", type="e", payload={"i": i})
        for i in range(5)
    ])

    events = await store.list_events("sess-since")
    assert len(events) == 5
    mid_id = events[2].id
    newer = await store.list_events("sess-since", since_id=mid_id)
    assert len(newer) == 2
    assert all(e.id > mid_id for e in newer)


@pytest.mark.asyncio
async def test_append_events_batch(store: SessionStore):
    """Batch append writes all events in a single transaction."""
    await store.create_session(SessionRecord(id="sess-batch"))
    batch = [
        EventRecord(
            session_id="sess-batch",
            type="assistant.delta",
            payload={"text": f"chunk-{i}"},
        )
        for i in range(50)
    ]
    await store.append_events_batch(batch)

    events = await store.list_events("sess-batch", limit=200)
    assert len(events) == 50
    assert events[0].payload["text"] == "chunk-0"
    assert events[-1].payload["text"] == "chunk-49"


@pytest.mark.asyncio
async def test_batch_write_performance(store: SessionStore):
    """100 events should batch-write in under 1 second."""
    await store.create_session(SessionRecord(id="sess-perf"))
    batch = [
        EventRecord(
            session_id="sess-perf",
            type="assistant.delta",
            payload={"text": f"perf-{i}", "data": "x" * 100},
        )
        for i in range(100)
    ]

    start = time.monotonic()
    await store.append_events_batch(batch)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"Batch write of 100 events took {elapsed:.3f}s (>1s)"

    events = await store.list_events("sess-perf", limit=200)
    assert len(events) == 100


# ── Approval tests ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_request_and_get(store: SessionStore):
    """Approval request round-trip."""
    await store.create_session(SessionRecord(id="sess-appr"))

    approval = ApprovalRecord(
        id="appr-001",
        session_id="sess-appr",
        action_kind="shell.command",
        action_payload={"command": "rm -rf /tmp/junk"},
        risk="medium",
        decision="pending",
    )
    await store.record_request(approval)

    fetched = await store.get_approval("appr-001")
    assert fetched is not None
    assert fetched.action_kind == "shell.command"
    assert fetched.decision == "pending"
    assert fetched.action_payload["command"] == "rm -rf /tmp/junk"


@pytest.mark.asyncio
async def test_record_decision(store: SessionStore):
    """record_decision updates the approval."""
    await store.create_session(SessionRecord(id="sess-dec"))
    await store.record_request(ApprovalRecord(
        id="appr-dec",
        session_id="sess-dec",
        action_kind="file.edit",
        risk="low",
    ))

    await store.record_decision("appr-dec", "approved", decided_by="user")

    fetched = await store.get_approval("appr-dec")
    assert fetched is not None
    assert fetched.decision == "approved"
    assert fetched.decided_by == "user"
    assert fetched.decided_at is not None


@pytest.mark.asyncio
async def test_find_remembered_decision(store: SessionStore):
    """find_remembered_decision returns approved action within TTL."""
    await store.create_session(SessionRecord(id="sess-remember"))

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    # Approved with future TTL — should be found
    await store.record_request(ApprovalRecord(
        id="appr-remember-yes",
        session_id="sess-remember",
        action_kind="shell.command",
        action_payload={"command": "npm test"},
        decision="approved",
        decided_at=datetime.now(timezone.utc).isoformat(),
        ttl_until=future,
    ))

    # Approved with past TTL — should NOT be found
    await store.record_request(ApprovalRecord(
        id="appr-remember-no",
        session_id="sess-remember",
        action_kind="shell.command",
        action_payload={"command": "dangerous cmd"},
        decision="approved",
        decided_at=datetime.now(timezone.utc).isoformat(),
        ttl_until=past,
    ))

    result = await store.find_remembered_decision("shell.command")
    assert result is not None
    assert result.id == "appr-remember-yes"

    # With fingerprint matching
    result2 = await store.find_remembered_decision("shell.command", "npm")
    assert result2 is not None

    result3 = await store.find_remembered_decision("shell.command", "dangerous")
    assert result3 is None  # expired TTL


@pytest.mark.asyncio
async def test_list_approvals_by_session(store: SessionStore):
    """list_approvals_by_session returns all approvals for a session."""
    await store.create_session(SessionRecord(id="sess-list-appr"))
    for i in range(3):
        await store.record_request(ApprovalRecord(
            id=f"appr-list-{i}",
            session_id="sess-list-appr",
            action_kind="file.edit",
        ))

    approvals = await store.list_approvals_by_session("sess-list-appr")
    assert len(approvals) == 3


# ── Foreign key / cascade tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_events_cascade_on_session_delete(store: SessionStore):
    """Deleting a session cascades to its events."""
    await store.create_session(SessionRecord(id="sess-cascade"))
    await store.append_event(EventRecord(
        session_id="sess-cascade", type="test", payload={}
    ))

    # Verify event exists
    events = await store.list_events("sess-cascade")
    assert len(events) == 1

    # Delete via raw SQL to test FK cascade
    await store.db.execute("DELETE FROM sessions WHERE id = 'sess-cascade'")
    await store.db.commit()

    # Event should be gone
    cursor = await store.db.execute(
        "SELECT COUNT(*) FROM events WHERE session_id = 'sess-cascade'"
    )
    count = (await cursor.fetchone())[0]
    assert count == 0


@pytest.mark.asyncio
async def test_approvals_cascade_on_session_delete(store: SessionStore):
    """Deleting a session cascades to its approvals."""
    await store.create_session(SessionRecord(id="sess-cascade-appr"))
    await store.record_request(ApprovalRecord(
        id="appr-cascade",
        session_id="sess-cascade-appr",
        action_kind="test",
    ))

    await store.db.execute("DELETE FROM sessions WHERE id = 'sess-cascade-appr'")
    await store.db.commit()

    cursor = await store.db.execute(
        "SELECT COUNT(*) FROM approvals WHERE session_id = 'sess-cascade-appr'"
    )
    count = (await cursor.fetchone())[0]
    assert count == 0
