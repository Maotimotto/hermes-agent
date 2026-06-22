"""
Session CRUD operations against the sessions table.

Calls go through the StoreDriver — backend-agnostic (SQLite/MySQL).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .driver import StoreDriver

logger = logging.getLogger(__name__)


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


def _row_to_session(row: dict) -> SessionRecord:
    """Convert a DB row (dict) to SessionRecord."""
    meta = row.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = None
    return SessionRecord(
        id=row["id"],
        runtime_kind=row.get("runtime_kind", "claude"),
        model=row.get("model"),
        repo_path=row.get("repo_path"),
        workspace_id=row.get("workspace_id"),
        status=row.get("status", "created"),
        started_at=row.get("started_at", ""),
        ended_at=row.get("ended_at"),
        metadata=meta,
    )


async def create_session(driver: StoreDriver, session: SessionRecord) -> None:
    await driver.execute(
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
    await driver.commit()


async def get_session(
    driver: StoreDriver, session_id: str
) -> SessionRecord | None:
    row = await driver.fetchone(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    )
    return _row_to_session(row) if row else None


async def list_sessions(
    driver: StoreDriver,
    *,
    status: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SessionRecord]:
    clauses = []
    params: list[object] = []

    if status:
        clauses.append("status = ?")
        params.append(status)

    query = (q or "").strip()
    if query:
        pattern = f"%{query.lower()}%"
        clauses.append(
            "("
            "LOWER(id) LIKE ? OR "
            "LOWER(COALESCE(metadata, '')) LIKE ? OR "
            "EXISTS ("
            "SELECT 1 FROM turns "
            "WHERE turns.session_id = sessions.id "
            "AND LOWER(COALESCE(turns.prompt, '')) LIKE ?"
            ")"
            ")"
        )
        params.extend([pattern, pattern, pattern])

    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    rows = await driver.fetchall(
        f"SELECT * FROM sessions {where}"
        "ORDER BY started_at DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )
    return [_row_to_session(r) for r in rows]


async def update_session_status(
    driver: StoreDriver,
    session_id: str,
    status: str,
    ended_at: str | None = None,
) -> None:
    if ended_at is None and status in ("completed", "failed", "cancelled", "stopped"):
        ended_at = datetime.now(timezone.utc).isoformat()
    await driver.execute(
        "UPDATE sessions SET status = ?, "
        "ended_at = COALESCE(?, ended_at) WHERE id = ?",
        (status, ended_at, session_id),
    )
    await driver.commit()
