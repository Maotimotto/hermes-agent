"""
Approval record write/query operations.

All DB access via StoreDriver — backend-agnostic.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .driver import StoreDriver

logger = logging.getLogger(__name__)


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


def _row_to_approval(row: dict) -> ApprovalRecord:
    payload = row.get("action_payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = None
    return ApprovalRecord(
        id=row["id"],
        session_id=row["session_id"],
        turn_id=row.get("turn_id"),
        action_kind=row["action_kind"],
        action_payload=payload,
        risk=row.get("risk"),
        decision=row.get("decision"),
        decided_at=row.get("decided_at"),
        decided_by=row.get("decided_by"),
        ttl_until=row.get("ttl_until"),
    )


async def record_request(
    driver: StoreDriver, approval: ApprovalRecord
) -> None:
    await driver.execute(
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
    await driver.commit()


async def record_decision(
    driver: StoreDriver,
    approval_id: str,
    decision: str,
    *,
    decided_by: str | None = None,
    ttl_until: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    await driver.execute(
        """
        UPDATE approvals
        SET decision = ?, decided_at = ?, decided_by = ?, ttl_until = ?
        WHERE id = ?
        """,
        (decision, now, decided_by, ttl_until, approval_id),
    )
    await driver.commit()


async def find_remembered_decision(
    driver: StoreDriver,
    action_kind: str,
    fingerprint: str | None = None,
) -> ApprovalRecord | None:
    now = datetime.now(timezone.utc).isoformat()
    if fingerprint:
        row = await driver.fetchone(
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
        row = await driver.fetchone(
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
    return _row_to_approval(row) if row else None


async def get_approval(
    driver: StoreDriver, approval_id: str
) -> ApprovalRecord | None:
    row = await driver.fetchone(
        "SELECT * FROM approvals WHERE id = ?", (approval_id,)
    )
    return _row_to_approval(row) if row else None


async def list_approvals_by_session(
    driver: StoreDriver, session_id: str
) -> list[ApprovalRecord]:
    # SQLite has implicit `rowid`; MySQL doesn't. Order by decided_at falls
    # back to NULL-last on pending, and id (TEXT) gives stable insertion order
    # when both sides assign UUIDs sequentially.
    rows = await driver.fetchall(
        "SELECT * FROM approvals WHERE session_id = ? "
        "ORDER BY COALESCE(decided_at, '~') ASC, id ASC",
        (session_id,),
    )
    return [_row_to_approval(r) for r in rows]
