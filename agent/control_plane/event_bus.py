"""In-process Event Bus for the V1.0.0 control plane.

Decoupled from the FastAPI app layer so non-HTTP callers
(Runtimes, ApprovalGate, internal services) can publish without
importing gateway code.

Three responsibilities:

  1. Pub/sub by ``session_id`` — subscribers get an ``asyncio.Queue`` that
     receives every event published for that session.
  2. Optional persistence hook — when a :class:`SessionStore` is attached,
     each published event is also written to the ``events`` table so the
     session can be replayed.
  3. Lightweight projection — adapters that wrap a :class:`HermesEvent`
     (or a plain dict) into a ``dict`` payload before fanout.  Keeps the
     bus type-agnostic so non-HermesEvent telemetry can still flow through.

Design note
-----------
We deliberately keep the in-process bus simple (no broker, no fan-in,
no backpressure beyond ``Queue``'s default unbounded behaviour).  For
V1.0.0 the daemon and the runtimes live in the same Python process —
swap this out for an aiokafka/Redis adapter later if we ever split.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, is_dataclass
from typing import Any, Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)


# ── Duck-typed store for the persistence hook ──────────────────────────────


class _EventStoreLike(Protocol):
    async def append_event(self, event: Any) -> None: ...


# ── Helpers ────────────────────────────────────────────────────────────────


def _normalise(event: Any) -> dict[str, Any]:
    """Coerce dataclasses / pydantic models / HermesEvent into a dict."""
    if isinstance(event, dict):
        return event
    if is_dataclass(event):
        return asdict(event)
    # pydantic v1/v2 compatibility
    for attr in ("model_dump", "dict"):
        fn = getattr(event, attr, None)
        if callable(fn):
            try:
                return dict(fn())
            except Exception:  # pragma: no cover
                pass
    # Fall back to vars()
    try:
        return dict(vars(event))
    except TypeError:
        return {"value": event}


# ── EventBus ───────────────────────────────────────────────────────────────


class EventBus:
    """Async in-process pub/sub for the control plane.

    Parameters
    ----------
    store : SessionStore-like | None
        When provided, every published event is also persisted to the
        store's ``events`` table.  ``append_event`` is best-effort — a
        write failure is logged but does not break subscriber fanout.
    """

    def __init__(self, store: _EventStoreLike | None = None) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        self._store = store
        # Custom listeners (callback-style) — invoked synchronously inside
        # publish().  Used for the daemon-side WebSocket broadcaster and
        # for tests.  Listeners must be cheap; offload anything slow to a
        # background task themselves.
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._async_listeners: list[Callable[[str, dict[str, Any]], Awaitable[None]]] = []

    # ── subscriber API (queue-based) ────────────────────────────────────

    def subscribe(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Return a queue that will receive every event for ``session_id``."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.setdefault(session_id, []).append(q)
        return q

    def unsubscribe(
        self, session_id: str, q: asyncio.Queue[dict[str, Any]]
    ) -> None:
        subs = self._subscribers.get(session_id)
        if not subs:
            return
        try:
            subs.remove(q)
        except ValueError:
            pass
        if not subs:
            del self._subscribers[session_id]

    # ── listener API (callback-based) ───────────────────────────────────

    def add_listener(
        self, callback: Callable[[str, dict[str, Any]], None]
    ) -> Callable[[], None]:
        """Register a sync callback.  Returns an unsubscribe function."""
        self._listeners.append(callback)

        def _off() -> None:
            try:
                self._listeners.remove(callback)
            except ValueError:
                pass

        return _off

    def add_async_listener(
        self, callback: Callable[[str, dict[str, Any]], Awaitable[None]]
    ) -> Callable[[], None]:
        """Register an async callback (scheduled on the event loop)."""
        self._async_listeners.append(callback)

        def _off() -> None:
            try:
                self._async_listeners.remove(callback)
            except ValueError:
                pass

        return _off

    # ── publish ─────────────────────────────────────────────────────────

    def publish(self, session_id: str, event: Any) -> None:
        """Publish ``event`` to every subscriber/listener for ``session_id``.

        Always non-blocking:
          - Queue subscribers get a ``put_nowait`` (queues are unbounded).
          - Sync listeners are invoked inline (must be cheap).
          - Async listeners are scheduled via ``asyncio.ensure_future``.
          - Persistence happens via ``asyncio.ensure_future`` so a slow
            DB write never blocks the producer.
        """
        payload = _normalise(event)
        # Always include the session_id for downstream consumers.
        payload.setdefault("session_id", session_id)

        # 1. fan-out to subscriber queues
        for q in list(self._subscribers.get(session_id, [])):
            try:
                q.put_nowait(payload)
            except Exception as exc:  # pragma: no cover
                logger.warning("[EventBus] queue put failed: %s", exc)

        # 2. sync listeners
        for cb in list(self._listeners):
            try:
                cb(session_id, payload)
            except Exception as exc:  # pragma: no cover
                logger.warning("[EventBus] sync listener raised: %s", exc)

        # 3. async listeners
        if self._async_listeners:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                for cb in list(self._async_listeners):
                    loop.create_task(self._safe_call(cb, session_id, payload))

        # 4. persistence hook
        if self._store is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                loop.create_task(self._safe_persist(payload))

    # ── private helpers ─────────────────────────────────────────────────

    async def _safe_call(
        self,
        cb: Callable[[str, dict[str, Any]], Awaitable[None]],
        session_id: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            await cb(session_id, payload)
        except Exception as exc:  # pragma: no cover
            logger.warning("[EventBus] async listener raised: %s", exc)

    async def _safe_persist(self, payload: dict[str, Any]) -> None:
        try:
            await self._store.append_event(payload)  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("[EventBus] persist failed: %s", exc)
