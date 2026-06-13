"""
Approval record write/query operations against the approvals table.

Supports recording approval requests, decisions, and looking up
remembered decisions (for ttl-based auto-approval).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)


# ── Minimal approval data (stand-in until approval types land) ───────────────

@dataclass
class ApprovalRecord:
    """Approval record for persistence."""
    id: str
    session_id: str
    action_kind: str
    action_payload: dict | None = None
    risk: str | None = None
    decision: str | None = "pending"
    turn_id: str | None = None
    decided_at: str | None = None
    decided_by: str | None = None
    ttl_until: str | None = None


def _row_to_approval(row: aiosqlite.Row) -> ApprovalRecord:
    """Convert a DB row to ApprovalRecord."""
    d = dict(row)
    payload = d.get("action_payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = None
    return ApprovalRecord(
        id=d["id"],
        session_id=d["session_id"],
        turn_id=d.get("turn_id"),
        action_kind=d["action_kind"],
        action_payload=payload,
        risk=d.get("risk"),
        decision=d.get("decision"),
        decided_at=d.get("decided_at"),
        decided_by=d.get("decided_by"),
        ttl_until=d.get("ttl_until"),
    )


# ── Write operations ─────────────────────────────────────────────────────────

async def record_request(
    db: aiosqlite.Connection, approval: ApprovalRecord
) -> None:
    """Insert a new approval request (decision defaults to 'pending')."""
    await db.execute(
        """
        INSERT INTO approvals (id, session_id, turn_id, action_kind,
                               action_payload, risk, decision,
                               decided_at, decided_by, ttl_until)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            approval.id,
            approval.session_id,
            approval.turn_id,
            approval.action_kind,
            json.dumps(approval.action_payload) if approval.action_payload else None,
            approval.risk,
            approval.decision or "pending",
            approval.decided_at,
            approval.decided_by,
            approval.ttl_until,
        ),
    )
    await db.commit()


async def record_decision(
    db: aiosqlite.Connection,
    approval_id: str,
    decision: str,
    *,
    decided_by: str | None = None,
    ttl_until: str | None = None,
) -> None:
    """Update an approval with a decision."""
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """
        UPDATE approvals
        SET decision = ?, decided_at = ?, decided_by = ?, ttl_until = ?
        WHERE id = ?
        """,
        (decision, now, decided_by, ttl_until, approval_id),
    )
    await db.commit()


async def find_remembered_decision(
    db: aiosqlite.Connection,
    action_kind: str,
    fingerprint: str | None = None,
) -> ApprovalRecord | None:
    """Find a previously-approved action whose ttl_until is still in the future.

    This enables "remember this decision" semantics — if the user approved
    an action and set a TTL, subsequent identical actions within that window
    are auto-approved.

    Parameters
    ----------
    action_kind : str
        The kind of action (e.g. "shell.command", "file.edit").
    fingerprint : str | None
        Optional fingerprint to match against action_payload.
        Used to distinguish between different commands of the same kind.
    """
    now = datetime.now(timezone.utc).isoformat()
    if fingerprint:
        cursor = await db.execute(
            """
            SELECT * FROM approvals
            WHERE action_kind = ?
              AND decision = 'approved'
              AND ttl_until IS NOT NULL
              AND ttl_until > ?
              AND action_payload LIKE ?
            ORDER BY decided_at DESC
            LIMIT 1
            """,
            (action_kind, now, f"%{fingerprint}%"),
        )
    else:
        cursor = await db.execute(
            """
            SELECT * FROM approvals
            WHERE action_kind = ?
              AND decision = 'approved'
              AND ttl_until IS NOT NULL
              AND ttl_until > ?
            ORDER BY decided_at DESC
            LIMIT 1
            """,
            (action_kind, now),
        )
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_approval(row)


async def get_approval(
    db: aiosqlite.Connection, approval_id: str
) -> ApprovalRecord | None:
    """Fetch a single approval by ID."""
    cursor = await db.execute(
        "SELECT * FROM approvals WHERE id = ?", (approval_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_approval(row)


async def list_approvals_by_session(
    db: aiosqlite.Connection, session_id: str
) -> list[ApprovalRecord]:
    """List all approvals for a session."""
    cursor = await db.execute(
        "SELECT * FROM approvals WHERE session_id = ? ORDER BY rowid",
        (session_id,),
    )
    rows = await cursor.fetchall()
    return [_row_to_approval(r) for r in rows]
