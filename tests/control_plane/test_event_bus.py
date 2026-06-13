"""Tests for the standalone EventBus (agent.control_plane.event_bus)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from agent.control_plane.event_bus import EventBus


# ── basic pub/sub ──────────────────────────────────────────────────────────


class TestSubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_receives_event(self) -> None:
        bus = EventBus()
        q = bus.subscribe("s1")
        bus.publish("s1", {"type": "tool.started", "id": "x"})
        ev = await asyncio.wait_for(q.get(), timeout=1.0)
        assert ev["type"] == "tool.started"
        assert ev["session_id"] == "s1"

    @pytest.mark.asyncio
    async def test_event_isolated_per_session(self) -> None:
        bus = EventBus()
        q1 = bus.subscribe("s1")
        q2 = bus.subscribe("s2")
        bus.publish("s1", {"type": "x"})
        assert q1.qsize() == 1
        assert q2.qsize() == 0

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_queue(self) -> None:
        bus = EventBus()
        q = bus.subscribe("s1")
        bus.unsubscribe("s1", q)
        bus.publish("s1", {"type": "x"})
        assert q.qsize() == 0


# ── normalisation ──────────────────────────────────────────────────────────


@dataclass
class Sample:
    type: str
    value: int


class TestNormalisation:
    @pytest.mark.asyncio
    async def test_dataclass_event_is_dict_in_queue(self) -> None:
        bus = EventBus()
        q = bus.subscribe("s1")
        bus.publish("s1", Sample(type="t", value=42))
        ev = await q.get()
        assert ev["type"] == "t"
        assert ev["value"] == 42
        assert ev["session_id"] == "s1"

    @pytest.mark.asyncio
    async def test_dict_passes_through(self) -> None:
        bus = EventBus()
        q = bus.subscribe("s1")
        bus.publish("s1", {"type": "x", "session_id": "explicit"})
        ev = await q.get()
        # Existing session_id wins over the publish() session_id
        assert ev["session_id"] == "explicit"


# ── listeners ──────────────────────────────────────────────────────────────


class TestListeners:
    def test_sync_listener_is_called(self) -> None:
        bus = EventBus()
        calls: list[tuple[str, dict[str, Any]]] = []
        bus.add_listener(lambda sid, ev: calls.append((sid, ev)))
        bus.publish("s1", {"type": "x"})
        assert calls == [("s1", {"type": "x", "session_id": "s1"})]

    def test_listener_unsubscribe(self) -> None:
        bus = EventBus()
        calls: list[Any] = []
        off = bus.add_listener(lambda sid, ev: calls.append(ev))
        off()
        bus.publish("s1", {"type": "x"})
        assert calls == []

    @pytest.mark.asyncio
    async def test_async_listener_scheduled(self) -> None:
        bus = EventBus()
        seen: list[dict[str, Any]] = []

        async def cb(sid: str, ev: dict[str, Any]) -> None:
            seen.append(ev)

        bus.add_async_listener(cb)
        bus.publish("s1", {"type": "x"})
        # Yield so the scheduled task runs.
        await asyncio.sleep(0.01)
        assert seen and seen[0]["type"] == "x"

    def test_sync_listener_exception_does_not_break_fanout(self) -> None:
        bus = EventBus()
        calls: list[Any] = []
        bus.add_listener(lambda sid, ev: (_ for _ in ()).throw(RuntimeError("boom")))
        bus.add_listener(lambda sid, ev: calls.append(ev))
        bus.publish("s1", {"type": "x"})
        # The second listener still ran.
        assert calls and calls[0]["type"] == "x"


# ── persistence hook ──────────────────────────────────────────────────────


class FakeStore:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.fail = False

    async def append_event(self, event: Any) -> None:
        if self.fail:
            raise RuntimeError("simulated db down")
        self.events.append(event)


class TestPersistenceHook:
    @pytest.mark.asyncio
    async def test_event_persisted_when_store_attached(self) -> None:
        store = FakeStore()
        bus = EventBus(store=store)
        bus.publish("s1", {"type": "tool.started"})
        await asyncio.sleep(0.01)
        assert len(store.events) == 1
        assert store.events[0]["type"] == "tool.started"

    @pytest.mark.asyncio
    async def test_store_failure_does_not_break_pub(self) -> None:
        store = FakeStore()
        store.fail = True
        bus = EventBus(store=store)
        q = bus.subscribe("s1")
        bus.publish("s1", {"type": "tool.started"})
        # Subscriber still got the event even though persist crashed.
        ev = await asyncio.wait_for(q.get(), timeout=1.0)
        assert ev["type"] == "tool.started"
        await asyncio.sleep(0.01)
        assert store.events == []
