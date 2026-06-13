"""
Session Store — aggregated facade over sessions/events/approvals tables.

Usage::

    from agent.control_plane.store import SessionStore

    store = SessionStore(db_path="/tmp/test.db")
    await store.init()
    # ... use store.create_session(), store.append_event(), etc.
    await store.close()
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import aiosqlite

from .db import create_database
from .migrate import run_migrations

from .sessions import (
    SessionRecord,
    create_session as _create_session,
    get_session as _get_session,
    list_sessions as _list_sessions,
    update_session_status as _update_session_status,
)
from .events import (
    EventRecord,
    append_event as _append_event,
    append_events_batch as _append_events_batch,
    list_events as _list_events,
)
from .approvals import (
    ApprovalRecord,
    record_request as _record_request,
    record_decision as _record_decision,
    find_remembered_decision as _find_remembered_decision,
    get_approval as _get_approval,
    list_approvals_by_session as _list_approvals_by_session,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SessionStore",
    "SessionRecord",
    "EventRecord",
    "ApprovalRecord",
]


class SessionStore:
    """Unified async facade for sessions / events / approvals.

    Wraps a single aiosqlite connection with WAL mode, foreign keys,
    and busy_timeout. All public methods are async.
    """

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Open connection and run migrations. Call once at startup."""
        self._db = await create_database(self._db_path)
        await run_migrations(self._db)

    @property
    def db(self) -> aiosqlite.Connection:
        """Access the underlying connection (for advanced use)."""
        if self._db is None:
            raise RuntimeError("SessionStore not initialized — call await store.init()")
        return self._db

    # ── Sessions ─────────────────────────────────────────────────────────────

    async def create_session(self, session: SessionRecord) -> None:
        await _create_session(self.db, session)

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return await _get_session(self.db, session_id)

    async def list_sessions(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionRecord]:
        return await _list_sessions(self.db, status=status, limit=limit, offset=offset)

    async def update_session_status(
        self, session_id: str, status: str, ended_at: str | None = None
    ) -> None:
        await _update_session_status(self.db, session_id, status, ended_at=ended_at)

    # ── Events ───────────────────────────────────────────────────────────────

    async def append_event(self, event: EventRecord) -> None:
        await _append_event(self.db, event)

    async def append_events_batch(self, events: list[EventRecord]) -> None:
        await _append_events_batch(self.db, events)

    async def list_events(
        self,
        session_id: str,
        *,
        since_id: int | None = None,
        types: list[str] | None = None,
        limit: int = 100,
    ) -> list[EventRecord]:
        return await _list_events(
            self.db, session_id, since_id=since_id, types=types, limit=limit
        )

    # ── Approvals ────────────────────────────────────────────────────────────

    async def record_request(self, approval: ApprovalRecord) -> None:
        await _record_request(self.db, approval)

    async def record_decision(
        self,
        approval_id: str,
        decision: str,
        *,
        decided_by: str | None = None,
        ttl_until: str | None = None,
    ) -> None:
        await _record_decision(
            self.db, approval_id, decision, decided_by=decided_by, ttl_until=ttl_until
        )

    async def find_remembered_decision(
        self, action_kind: str, fingerprint: str | None = None
    ) -> ApprovalRecord | None:
        return await _find_remembered_decision(self.db, action_kind, fingerprint)

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        return await _get_approval(self.db, approval_id)

    async def list_approvals_by_session(
        self, session_id: str
    ) -> list[ApprovalRecord]:
        return await _list_approvals_by_session(self.db, session_id)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None
