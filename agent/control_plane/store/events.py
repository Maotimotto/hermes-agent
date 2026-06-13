"""
Event write/query operations against the events table.

Supports single append, batch append (single transaction), and
paginated queries with optional type filtering.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)


# ── Minimal event data (stand-in until hermes_event.py lands) ────────────────

@dataclass
class EventRecord:
    """Lightweight event for store operations."""
    session_id: str
    type: str
    payload: dict
    turn_id: str | None = None
    created_at: str = ""
    # Optional numeric id — auto-assigned by DB for new records
    id: int | None = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


def _row_to_event(row: aiosqlite.Row) -> EventRecord:
    """Convert a DB row to EventRecord."""
    d = dict(row)
    payload = d.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}
    return EventRecord(
        id=d.get("id"),
        session_id=d["session_id"],
        turn_id=d.get("turn_id"),
        type=d["type"],
        payload=payload,
        created_at=d.get("created_at", ""),
    )


# ── Write operations ─────────────────────────────────────────────────────────

async def append_event(db: aiosqlite.Connection, event: EventRecord) -> None:
    """Write a single event to the events table."""
    await db.execute(
        """
        INSERT INTO events (session_id, turn_id, type, payload, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            event.session_id,
            event.turn_id,
            event.type,
            json.dumps(event.payload),
            event.created_at,
        ),
    )
    await db.commit()


async def append_events_batch(
    db: aiosqlite.Connection, events: list[EventRecord]
) -> None:
    """Batch-write events in a single transaction for performance."""
    if not events:
        return
    await db.executemany(
        """
        INSERT INTO events (session_id, turn_id, type, payload, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                e.session_id,
                e.turn_id,
                e.type,
                json.dumps(e.payload),
                e.created_at,
            )
            for e in events
        ],
    )
    await db.commit()


# ── Query operations ─────────────────────────────────────────────────────────

async def list_events(
    db: aiosqlite.Connection,
    session_id: str,
    *,
    since_id: int | None = None,
    types: list[str] | None = None,
    limit: int = 100,
) -> list[EventRecord]:
    """Query events for a session with optional filters.

    Parameters
    ----------
    session_id : str
        The session to query events for.
    since_id : int | None
        If set, only return events with id > since_id (for incremental polling).
    types : list[str] | None
        If set, filter to these event types only.
    limit : int
        Max rows to return (default 100).
    """
    conditions = ["session_id = ?"]
    params: list = [session_id]

    if since_id is not None:
        conditions.append("id > ?")
        params.append(since_id)

    if types:
        placeholders = ",".join("?" for _ in types)
        conditions.append(f"type IN ({placeholders})")
        params.extend(types)

    params.append(limit)
    where = " AND ".join(conditions)

    cursor = await db.execute(
        f"SELECT * FROM events WHERE {where} ORDER BY id ASC LIMIT ?",
        params,
    )
    rows = await cursor.fetchall()
    return [_row_to_event(r) for r in rows]
