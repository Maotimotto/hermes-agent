"""Approval routes for the Daemon API control plane.

Endpoints:
  GET  /approvals                — list approvals (optionally filter by status)
  POST /approvals/{id}/decision — resolve an approval
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from agent.control_plane.approval import ApprovalGate
from agent.control_plane.store import ApprovalRecord, SessionStore
from gateway.control_plane.deps import (
    AppState,
    EventBus,
    get_app_state,
    get_approval_gate,
    get_event_bus,
    get_store,
)
from gateway.control_plane.schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/approvals", tags=["approvals"])


# ── helpers ──────────────────────────────────────────────────────────────────


def _approval_to_response(a: ApprovalRecord) -> ApprovalResponse:
    return ApprovalResponse(
        id=a.id,
        session_id=a.session_id,
        turn_id=a.turn_id,
        action_kind=a.action_kind,
        action_payload=a.action_payload,
        risk=a.risk,
        decision=a.decision,
        decided_at=a.decided_at,
        decided_by=a.decided_by,
        ttl_until=a.ttl_until,
    )


# ── GET /approvals ───────────────────────────────────────────────────────────


@router.get("", response_model=list[ApprovalResponse])
async def list_approvals(
    status: Optional[str] = Query(None, description="Filter by decision status (e.g. pending)"),
    store: SessionStore = Depends(get_store),
) -> list[ApprovalResponse]:
    """List approvals across all sessions, optionally filtered by status.

    When ``status=pending``, returns only unresolved approvals.
    """
    # We need a list_approvals with status filter — the store layer only has
    # list_approvals_by_session.  For now we iterate all known sessions.
    # A future optimization adds a cross-session list to the store layer.
    # For this MVP we do a simple scan across all approvals in the DB.
    cursor = await store.db.execute(
        "SELECT * FROM approvals ORDER BY rowid DESC LIMIT 500"
    )
    rows = await cursor.fetchall()
    approvals = []
    for row in rows:
        import json

        d = dict(row)
        payload = d.get("action_payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                payload = None
        a = ApprovalRecord(
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
        if status and a.decision != status:
            continue
        approvals.append(_approval_to_response(a))
    return approvals


# ── POST /approvals/{id}/decision ────────────────────────────────────────────


@router.post("/{approval_id}/decision", response_model=ApprovalDecisionResponse)
async def resolve_approval(
    approval_id: str,
    body: ApprovalDecisionRequest,
    store: SessionStore = Depends(get_store),
    gate: ApprovalGate = Depends(get_approval_gate),
) -> ApprovalDecisionResponse:
    """Resolve an approval request via the ApprovalGate.

    Routes through ``ApprovalGate.resolve`` so that any runtime currently
    awaiting this approval is woken up.  The gate also handles persistence
    and ``approval.resolved`` event publication.
    """
    existing = await store.get_approval(approval_id)
    if existing is None:
        raise HTTPException(
            status_code=404, detail=f"Approval not found: {approval_id}"
        )

    await gate.resolve(
        approval_id,
        body.decision,
        decided_by=body.decided_by or "user",
        ttl_seconds=body.ttl,
    )

    return ApprovalDecisionResponse(
        id=approval_id,
        decision=body.decision,
        decided_at=datetime.now(timezone.utc).isoformat(),
    )
