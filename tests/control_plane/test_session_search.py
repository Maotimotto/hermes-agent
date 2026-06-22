from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from agent.control_plane.store import SessionRecord, SessionStore


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    db_path = tmp_path / "session_search.db"
    s = SessionStore(db_path=db_path)
    await s.init()
    yield s
    await s.close()


async def _add_session(
    store: SessionStore,
    session_id: str,
    *,
    prompt: str | None = None,
    metadata: dict | None = None,
) -> None:
    await store.create_session(SessionRecord(id=session_id, metadata=metadata))
    if prompt is not None:
        await store.driver.execute(
            """
            INSERT INTO turns (id, session_id, prompt, status, started_at)
            VALUES (?, ?, ?, 'completed', ?)
            """,
            (f"turn-{session_id}", session_id, prompt, "2026-06-22T00:00:00+00:00"),
        )
        await store.driver.commit()


@pytest.mark.asyncio
async def test_list_sessions_q_matches_turn_prompt(store: SessionStore):
    await _add_session(store, "sess_prompt_hit", prompt="Implement billing report")
    await _add_session(store, "sess_prompt_miss", prompt="Refactor dashboard")

    sessions = await store.list_sessions(q="billing")

    assert [s.id for s in sessions] == ["sess_prompt_hit"]


@pytest.mark.asyncio
async def test_list_sessions_q_matches_session_id(store: SessionStore):
    await _add_session(store, "sess_alpha_ticket")
    await _add_session(store, "sess_beta_ticket")

    sessions = await store.list_sessions(q="alpha")

    assert [s.id for s in sessions] == ["sess_alpha_ticket"]


@pytest.mark.asyncio
async def test_list_sessions_q_is_case_insensitive(store: SessionStore):
    await _add_session(store, "sess_case_hit", prompt="Fix PAYMENT webhook")
    await _add_session(store, "sess_case_miss", prompt="Update docs")

    sessions = await store.list_sessions(q="payment")

    assert [s.id for s in sessions] == ["sess_case_hit"]


@pytest.mark.asyncio
async def test_list_sessions_empty_q_matches_no_q(store: SessionStore):
    await _add_session(store, "sess_empty_a")
    await _add_session(store, "sess_empty_b")

    without_q = await store.list_sessions()
    with_empty_q = await store.list_sessions(q="")

    assert [s.id for s in with_empty_q] == [s.id for s in without_q]
