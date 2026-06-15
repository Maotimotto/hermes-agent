"""
Event write/query operations against the events table.

All access via StoreDriver — backend-agnostic.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .driver import StoreDriver

logger = logging.getLogger(__name__)


@dataclass
class EventRecord:
    """Lightweight event for store operations."""
    session_id: str
    type: str
    payload: dict
    turn_id: str | None = None
    created_at: str = ""
    id: int | None = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


def _row_to_event(row: dict) -> EventRecord:
    payload = row.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}
    return EventRecord(
        id=row.get("id"),
        session_id=row["session_id"],
        turn_id=row.get("turn_id"),
        type=row["type"],
        payload=payload,
        created_at=row.get("created_at", ""),
    )


async def append_event(driver: StoreDriver, event: EventRecord) -> None:
    await driver.execute(
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
    await driver.commit()


async def append_events_batch(
    driver: StoreDriver, events: list[EventRecord]
) -> None:
    if not events:
        return
    await driver.executemany(
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
    await driver.commit()


async def list_events(
    driver: StoreDriver,
    session_id: str,
    *,
    since_id: int | None = None,
    types: list[str] | None = None,
    limit: int = 100,
) -> list[EventRecord]:
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

    rows = await driver.fetchall(
        f"SELECT * FROM events WHERE {where} ORDER BY id ASC LIMIT ?",
        params,
    )
    return [_row_to_event(r) for r in rows]
