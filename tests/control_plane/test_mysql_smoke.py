"""
MySQL backend smoke test.

Skips when:
  - aiomysql isn't installed, or
  - HERMES_CP_TEST_MYSQL_URL is not set.

Set HERMES_CP_TEST_MYSQL_URL to a writable DSN with `?prefix=hcptest_`
before running, e.g.:
    export HERMES_CP_TEST_MYSQL_URL="mysql://commentai:comment_ai_pass@127.0.0.1:3306/comment_ai?prefix=hcptest_"

The test creates rows under that prefix, runs the full CRUD surface,
then drops only its own tables — does NOT touch other tables in the db.
"""

from __future__ import annotations

import os
import uuid

import pytest


pytestmark = [pytest.mark.asyncio]

MYSQL_URL = os.environ.get("HERMES_CP_TEST_MYSQL_URL")

aiomysql = pytest.importorskip("aiomysql")  # noqa: F401

if not MYSQL_URL:
    pytest.skip(
        "HERMES_CP_TEST_MYSQL_URL not set — skipping live MySQL smoke test",
        allow_module_level=True,
    )

from agent.control_plane.store import (  # noqa: E402
    SessionStore,
    SessionRecord,
    EventRecord,
    ApprovalRecord,
)
from agent.control_plane.store.driver import (  # noqa: E402
    KNOWN_TABLES,
    create_driver,
)


async def _drop_prefixed_tables(url: str) -> None:
    """Best-effort cleanup of our test tables (ignores missing)."""
    from urllib.parse import urlparse, parse_qs

    parsed = urlparse(url)
    prefix = parse_qs(parsed.query).get("prefix", [""])[0]
    drv = await create_driver(url)
    try:
        # FK-safe order: events/approvals first (children), then turns, sessions
        order = ("events", "approvals", "turns", "sessions", "_migrations")
        for tbl in order:
            await drv.execute(f"DROP TABLE IF EXISTS {prefix}{tbl}")
        await drv.commit()
    finally:
        await drv.close()


import pytest_asyncio


@pytest_asyncio.fixture()
async def store():
    # Clean slate.
    await _drop_prefixed_tables(MYSQL_URL)
    s = SessionStore(db_path=MYSQL_URL)
    await s.init()
    yield s
    await s.close()
    await _drop_prefixed_tables(MYSQL_URL)


async def test_mysql_session_crud_roundtrip(store):
    sid = f"sess_{uuid.uuid4().hex[:12]}"
    rec = SessionRecord(
        id=sid,
        runtime_kind="codex",
        model="gpt-5.5",
        repo_path="/tmp/x",
        status="created",
        metadata={"hint": "hello"},
    )
    await store.create_session(rec)

    got = await store.get_session(sid)
    assert got is not None
    assert got.id == sid
    assert got.runtime_kind == "codex"
    assert got.metadata == {"hint": "hello"}

    await store.update_session_status(sid, "running")
    got = await store.get_session(sid)
    assert got.status == "running"

    listed = await store.list_sessions(limit=10)
    assert any(s.id == sid for s in listed)


async def test_mysql_event_append_and_query(store):
    sid = f"sess_{uuid.uuid4().hex[:12]}"
    await store.create_session(SessionRecord(id=sid, runtime_kind="codex"))

    e1 = EventRecord(session_id=sid, type="turn.started", payload={"i": 1})
    e2 = EventRecord(session_id=sid, type="assistant.message",
                     payload={"text": "hi"})
    await store.append_event(e1)
    await store.append_event(e2)

    rows = await store.list_events(sid, limit=10)
    assert len(rows) == 2
    assert rows[0].type == "turn.started"
    assert rows[1].payload == {"text": "hi"}

    # since_id pagination
    last_id = rows[0].id
    assert last_id is not None
    tail = await store.list_events(sid, since_id=last_id, limit=10)
    assert len(tail) == 1
    assert tail[0].type == "assistant.message"


async def test_mysql_approvals_remembered_decision(store):
    sid = f"sess_{uuid.uuid4().hex[:12]}"
    await store.create_session(SessionRecord(id=sid, runtime_kind="codex"))

    aid = f"appr_{uuid.uuid4().hex[:12]}"
    await store.record_request(ApprovalRecord(
        id=aid,
        session_id=sid,
        action_kind="shell.command",
        action_payload={"cmd": "ls", "fingerprint": "ls-cwd"},
    ))
    # Far-future TTL
    from datetime import datetime, timezone, timedelta
    ttl = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    await store.record_decision(aid, "approved", decided_by="user", ttl_until=ttl)

    found = await store.find_remembered_decision("shell.command", fingerprint="ls-cwd")
    assert found is not None
    assert found.id == aid
    assert found.decision == "approved"
