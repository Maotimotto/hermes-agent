"""
Session Store — aggregated facade over sessions/events/approvals tables.

Backend is picked by the URL:
  - sqlite:///abs/path.db                    (default, dev/test)
  - mysql://user:pass@host:port/db?prefix=hcp_   (production)

Usage::

    from agent.control_plane.store import SessionStore

    # SQLite (default)
    store = SessionStore(db_path="/tmp/test.db")
    await store.init()

    # MySQL
    store = SessionStore(db_path="mysql://commentai:***@127.0.0.1:3306/comment_ai?prefix=hcp_")
    await store.init()

The HERMES_CP_DB_URL env var overrides db_path when unset.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .driver import StoreDriver, create_driver, DEFAULT_DB_PATH
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
    "DEFAULT_DB_PATH",
]


class SessionStore:
    """Unified async facade for sessions / events / approvals.

    Wraps a StoreDriver (SQLite or MySQL). All public methods are async.
    """

    def __init__(self, db_path: str | Path | None = None):
        # Env var fallback when caller didn't pass an explicit URL.
        if db_path is None:
            db_path = os.environ.get("HERMES_CP_DB_URL")
        self._db_path = db_path
        self._driver: StoreDriver | None = None

    async def init(self) -> None:
        """Open driver and run migrations. Call once at startup."""
        self._driver = await create_driver(self._db_path)
        await run_migrations(self._driver)

    @property
    def driver(self) -> StoreDriver:
        if self._driver is None:
            raise RuntimeError("SessionStore not initialized — call await store.init()")
        return self._driver

    @property
    def db(self) -> StoreDriver:
        """Back-compat alias for legacy callers that accessed `.db`."""
        return self.driver

    # ── Sessions ─────────────────────────────────────────────────────────────

    async def create_session(self, session: SessionRecord) -> None:
        await _create_session(self.driver, session)

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return await _get_session(self.driver, session_id)

    async def list_sessions(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionRecord]:
        return await _list_sessions(
            self.driver, status=status, limit=limit, offset=offset
        )

    async def update_session_status(
        self, session_id: str, status: str, ended_at: str | None = None
    ) -> None:
        await _update_session_status(
            self.driver, session_id, status, ended_at=ended_at
        )

    # ── Events ───────────────────────────────────────────────────────────────

    async def append_event(self, event: EventRecord) -> None:
        await _append_event(self.driver, event)

    async def append_events_batch(self, events: list[EventRecord]) -> None:
        await _append_events_batch(self.driver, events)

    async def list_events(
        self,
        session_id: str,
        *,
        since_id: int | None = None,
        types: list[str] | None = None,
        limit: int = 100,
    ) -> list[EventRecord]:
        return await _list_events(
            self.driver, session_id, since_id=since_id, types=types, limit=limit
        )

    # ── Approvals ────────────────────────────────────────────────────────────

    async def record_request(self, approval: ApprovalRecord) -> None:
        await _record_request(self.driver, approval)

    async def record_decision(
        self,
        approval_id: str,
        decision: str,
        *,
        decided_by: str | None = None,
        ttl_until: str | None = None,
    ) -> None:
        await _record_decision(
            self.driver, approval_id, decision,
            decided_by=decided_by, ttl_until=ttl_until,
        )

    async def find_remembered_decision(
        self, action_kind: str, fingerprint: str | None = None
    ) -> ApprovalRecord | None:
        return await _find_remembered_decision(self.driver, action_kind, fingerprint)

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        return await _get_approval(self.driver, approval_id)

    async def list_approvals_by_session(
        self, session_id: str
    ) -> list[ApprovalRecord]:
        return await _list_approvals_by_session(self.driver, session_id)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None
