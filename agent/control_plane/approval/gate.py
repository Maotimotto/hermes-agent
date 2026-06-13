"""ApprovalGate — unified approval entry point for V1.0.0 control plane.

Unifies what used to live in three independent files
(``tools/approval.py``, ``agent/shell_hooks.py``, ``agent/file_safety.py``)
into a single async gate that:

  1. Runs the action through the policy chain (``policies.py``) to infer risk.
  2. Checks user whitelist (fast path → approved).
  3. Checks user blacklist (fast path → denied).
  4. Checks remembered decisions (SessionStore TTL match → reuse).
  5. Otherwise → persists ``pending``, emits ``approval.requested`` on the
     EventBus, and returns an awaitable that the caller can ``await`` until
     the UI (or another resolver) submits a decision via :meth:`resolve`.

The gate is intentionally framework-agnostic — it talks to ``SessionStore``
through duck-typed methods and to the EventBus by calling ``publish()``.
Both can be mocked in tests without spinning up SQLite or FastAPI.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Protocol

from agent.control_plane.store import ApprovalRecord

from .policies import (
    ApprovalPolicy,
    DEFAULT_POLICIES,
    evaluate_policies,
)
from .types import ApprovalRequest, RiskLevel

logger = logging.getLogger(__name__)


# ── Result type ─────────────────────────────────────────────────────────────


@dataclass
class GateResult:
    """Outcome of :meth:`ApprovalGate.evaluate`.

    Attributes
    ----------
    decision : str
        One of ``approved`` / ``denied`` / ``pending``.
    approval_id : str
        The ID under which this request was persisted.
    risk : RiskLevel
        Inferred risk level.
    warning : str
        Human-readable warning (may be empty for low-risk).
    source : str
        Where the decision came from: ``whitelist`` / ``blacklist`` /
        ``remembered`` / ``policy`` / ``pending``.
    awaitable : asyncio.Future[str] | None
        Only set when ``decision == "pending"``. Caller can ``await`` it
        and will receive the final ``approved``/``denied`` once a resolver
        calls :meth:`ApprovalGate.resolve`.
    """

    decision: str
    approval_id: str
    risk: RiskLevel
    warning: str
    source: str
    awaitable: asyncio.Future | None = None


# ── Duck-typed dependencies ─────────────────────────────────────────────────


class _StoreLike(Protocol):
    async def record_request(self, approval: ApprovalRecord) -> None: ...
    async def record_decision(
        self,
        approval_id: str,
        decision: str,
        *,
        decided_by: str | None = ...,
        ttl_until: str | None = ...,
    ) -> None: ...
    async def find_remembered_decision(
        self,
        action_kind: str,
        fingerprint: str | None = ...,
    ) -> ApprovalRecord | None: ...
    async def get_approval(self, approval_id: str) -> ApprovalRecord | None: ...


class _EventBusLike(Protocol):
    def publish(self, session_id: str, event: dict[str, Any]) -> None: ...


# ── Fingerprinting (for remembered decisions) ───────────────────────────────


def fingerprint(req: ApprovalRequest) -> str:
    """Compute a stable fingerprint for remember-decision matching.

    The fingerprint is the action_kind plus a normalised representation
    of the salient payload fields. Two requests with the same fingerprint
    are considered "the same action" for TTL reuse purposes.
    """
    p = req.action_payload or {}
    cmd = str(p.get("command") or p.get("cmd") or "")
    path = str(p.get("path") or p.get("file") or "")
    url = str(p.get("url") or "")
    # Collapse whitespace runs so trivial reformatting doesn't break matching.
    parts = [req.action_kind, cmd, path, url]
    blob = "|".join(parts)
    return re.sub(r"\s+", " ", blob).strip()


# ── ApprovalGate ────────────────────────────────────────────────────────────


class ApprovalGate:
    """Unified async approval gate.

    Parameters
    ----------
    store : SessionStore-like
        Used for persistence + remembered-decision lookup.
    event_bus : EventBus-like
        Used to publish ``approval.requested`` / ``approval.resolved`` events.
    policies : list[ApprovalPolicy] | None
        Custom policy chain (defaults to :data:`DEFAULT_POLICIES`).
    """

    def __init__(
        self,
        store: _StoreLike,
        event_bus: _EventBusLike,
        *,
        policies: list[ApprovalPolicy] | None = None,
    ) -> None:
        self._store = store
        self._bus = event_bus
        self._policies = policies if policies is not None else DEFAULT_POLICIES
        self._whitelist: list[str] = []
        self._blacklist: list[str] = []
        # approval_id -> future to wake when resolved
        self._pending: dict[str, asyncio.Future] = {}

    # ── whitelist / blacklist ───────────────────────────────────────────────

    def add_whitelist(self, pattern: str) -> None:
        if pattern and pattern not in self._whitelist:
            self._whitelist.append(pattern)

    def add_blacklist(self, pattern: str) -> None:
        if pattern and pattern not in self._blacklist:
            self._blacklist.append(pattern)

    def _matches(self, req: ApprovalRequest, patterns: list[str]) -> bool:
        if not patterns:
            return False
        p = req.action_payload or {}
        haystack = " ".join(
            str(v)
            for v in (
                p.get("command"),
                p.get("cmd"),
                p.get("path"),
                p.get("url"),
                req.action_kind,
            )
            if v
        )
        return any(pat in haystack for pat in patterns)

    # ── main entry: evaluate ────────────────────────────────────────────────

    async def evaluate(self, req: ApprovalRequest) -> GateResult:
        """Decide what to do with the request.

        See class docstring for the full pipeline.
        """
        # 1. Policy chain → risk + warning
        risk, warning, _matched = evaluate_policies(req, self._policies)
        req.risk = risk

        # 2. Whitelist (fast approve)
        if self._matches(req, self._whitelist):
            return await self._auto(req, "approved", risk, warning, "whitelist")

        # 3. Blacklist (fast deny)
        if self._matches(req, self._blacklist):
            return await self._auto(req, "denied", risk, warning, "blacklist")

        # 4. Remembered decision (TTL still valid)
        try:
            remembered = await self._store.find_remembered_decision(
                action_kind=req.action_kind,
                fingerprint=fingerprint(req),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[ApprovalGate] remembered lookup failed: %s", exc)
            remembered = None
        if remembered is not None and remembered.decision == "approved":
            return await self._auto(req, "approved", risk, warning, "remembered")

        # 5. Pending → persist + emit event + wait
        record = ApprovalRecord(
            id=req.id,
            session_id=req.session_id,
            turn_id=req.turn_id or None,
            action_kind=req.action_kind,
            action_payload=req.action_payload,
            risk=risk.value,
            decision="pending",
        )
        await self._store.record_request(record)

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req.id] = fut

        self._bus.publish(
            req.session_id,
            {
                "type": "approval.requested",
                "approval_id": req.id,
                "session_id": req.session_id,
                "turn_id": req.turn_id,
                "action_kind": req.action_kind,
                "action_payload": req.action_payload,
                "risk": risk.value,
                "warning": warning,
                "created_at": req.created_at,
            },
        )
        return GateResult(
            decision="pending",
            approval_id=req.id,
            risk=risk,
            warning=warning,
            source="pending",
            awaitable=fut,
        )

    # ── resolver: called when the user (or another agent) decides ───────────

    async def resolve(
        self,
        approval_id: str,
        decision: str,
        *,
        decided_by: str = "user",
        ttl_seconds: int | None = None,
    ) -> None:
        """Record a decision and wake any awaiter.

        ``decision`` must be ``approved`` or ``denied``.
        ``ttl_seconds`` enables remember-this-decision for the matching
        fingerprint.
        """
        if decision not in ("approved", "denied"):
            raise ValueError(f"Invalid decision: {decision!r}")

        ttl_until: str | None = None
        if ttl_seconds and ttl_seconds > 0 and decision == "approved":
            ttl_until = (
                datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
            ).isoformat()

        await self._store.record_decision(
            approval_id,
            decision,
            decided_by=decided_by,
            ttl_until=ttl_until,
        )

        # Look up the record so we can emit a meaningful resolved event.
        rec = None
        try:
            rec = await self._store.get_approval(approval_id)
        except Exception:
            rec = None
        if rec is not None:
            self._bus.publish(
                rec.session_id,
                {
                    "type": "approval.resolved",
                    "approval_id": approval_id,
                    "session_id": rec.session_id,
                    "turn_id": rec.turn_id,
                    "decision": decision,
                    "decided_by": decided_by,
                    "ttl_until": ttl_until,
                },
            )

        fut = self._pending.pop(approval_id, None)
        if fut is not None and not fut.done():
            fut.set_result(decision)

    # ── private helpers ─────────────────────────────────────────────────────

    async def _auto(
        self,
        req: ApprovalRequest,
        decision: str,
        risk: RiskLevel,
        warning: str,
        source: str,
    ) -> GateResult:
        """Persist a request that was auto-resolved (no UI prompt)."""
        record = ApprovalRecord(
            id=req.id,
            session_id=req.session_id,
            turn_id=req.turn_id or None,
            action_kind=req.action_kind,
            action_payload=req.action_payload,
            risk=risk.value,
            decision=decision,
            decided_by=source,
            decided_at=datetime.now(timezone.utc).isoformat(),
        )
        await self._store.record_request(record)
        return GateResult(
            decision=decision,
            approval_id=req.id,
            risk=risk,
            warning=warning,
            source=source,
        )
