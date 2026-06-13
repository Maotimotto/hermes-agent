"""
Session CRUD operations against the sessions table.

Depends on db.py for connection management.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)


# ── Minimal data types (stand-in until hermes_event.py lands) ────────────────

@dataclass
class SessionRecord:
    """Session record for persistence."""
    id: str
    runtime_kind: str = "claude"
    model: str | None = None
    repo_path: str | None = None
    workspace_id: str | None = None
    status: str = "created"
    started_at: str = ""
    ended_at: str | None = None
    metadata: dict | None = None

    def __post_init__(self):
        if not self.started_at:
            self.started_at = datetime.now(timezone.utc).isoformat()


def _row_to_session(row: aiosqlite.Row) -> SessionRecord:
    """Convert a DB row to SessionRecord."""
    d = dict(row)
    meta = d.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = None
    return SessionRecord(
        id=d["id"],
        runtime_kind=d.get("runtime_kind", "claude"),
        model=d.get("model"),
        repo_path=d.get("repo_path"),
        workspace_id=d.get("workspace_id"),
        status=d.get("status", "created"),
        started_at=d.get("started_at", ""),
        ended_at=d.get("ended_at"),
        metadata=meta,
    )


# ── CRUD functions ───────────────────────────────────────────────────────────

async def create_session(db: aiosqlite.Connection, session: SessionRecord) -> None:
    """Insert a new session record."""
    await db.execute(
        """
        INSERT INTO sessions (id, runtime_kind, model, repo_path, workspace_id,
                              status, started_at, ended_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session.id,
            session.runtime_kind,
            session.model,
            session.repo_path,
            session.workspace_id,
            session.status,
            session.started_at,
            session.ended_at,
            json.dumps(session.metadata) if session.metadata else None,
        ),
    )
    await db.commit()


async def get_session(
    db: aiosqlite.Connection, session_id: str
) -> SessionRecord | None:
    """Fetch a session by ID, or None if not found."""
    cursor = await db.execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_session(row)


async def list_sessions(
    db: aiosqlite.Connection,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SessionRecord]:
    """List sessions with optional status filter and pagination."""
    if status:
        cursor = await db.execute(
            "SELECT * FROM sessions WHERE status = ? ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (status, limit, offset),
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    rows = await cursor.fetchall()
    return [_row_to_session(r) for r in rows]


async def update_session_status(
    db: aiosqlite.Connection,
    session_id: str,
    status: str,
    ended_at: str | None = None,
) -> None:
    """Update session status (and optionally ended_at)."""
    if ended_at is None and status in ("completed", "failed", "cancelled", "stopped"):
        ended_at = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE sessions SET status = ?, ended_at = COALESCE(?, ended_at) WHERE id = ?",
        (status, ended_at, session_id),
    )
    await db.commit()
