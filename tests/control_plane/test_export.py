"""Tests for session log export — V1.1 P1 基础日志导出.

覆盖：
  - JSON 端点 200 + Content-Disposition
  - Markdown 端点 200 + Content-Disposition
  - 不存在 session 404 (JSON / MD)
  - JSON 内容含 events / approvals / file_changes
  - Markdown 含必要 section
  - file.changed 聚合（同 path 取最新）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from agent.control_plane.store import ApprovalRecord, EventRecord, SessionStore
from tests.control_plane.test_daemon_api import (  # noqa: F401 — fixture reuse
    MockRuntime,
    app,
    app_pair,
    cp_app,
    client,
    mock_runtime,
)


# ── 小工具：创建 session + 直接写 store ─────────────────────────────────────

def _create_session(client: TestClient) -> str:
    resp = client.post(
        "/control-plane/sessions",
        json={"runtime_kind": "claude", "model": "claude-sonnet-4-6"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _store(cp_app) -> SessionStore:
    return cp_app.state.cp.store  # type: ignore[attr-defined,no-any-return]


async def _seed_events_and_approvals(store: SessionStore, sid: str) -> None:
    # 三种典型事件 + 同一 path 两次 file.changed（验证聚合取最新）
    # turn_id 设 None 避免 FK 约束（turns 表没有 row）
    events = [
        EventRecord(
            id=0,
            session_id=sid,
            turn_id=None,
            type="turn.started",
            payload={"prompt": "hello"},
            created_at="",
        ),
        EventRecord(
            id=0,
            session_id=sid,
            turn_id=None,
            type="assistant.message",
            payload={"text": "hi there"},
            created_at="",
        ),
        EventRecord(
            id=0,
            session_id=sid,
            turn_id=None,
            type="file.changed",
            payload={
                "path": "foo.py",
                "operation": "create",
                "diff": "--- /dev/null\n+++ b/foo.py\n+v1\n",
            },
            created_at="",
        ),
        EventRecord(
            id=0,
            session_id=sid,
            turn_id=None,
            type="file.changed",
            payload={
                "path": "foo.py",
                "operation": "edit",
                "diff": "--- a/foo.py\n+++ b/foo.py\n-v1\n+v2\n",
            },
            created_at="",
        ),
        EventRecord(
            id=0,
            session_id=sid,
            turn_id=None,
            type="turn.completed",
            payload={},
            created_at="",
        ),
    ]
    for e in events:
        await store.append_event(e)

    # 写一条 approval（id 含 "appr_" 前缀，模仿真实场景）
    approval = ApprovalRecord(
        id="appr_test_001",
        session_id=sid,
        turn_id=None,
        action_kind="bash_run",
        action_payload={"cmd": "rm -rf /"},
        risk="high",
        decision="denied",
        decided_at="",
        decided_by="user",
        ttl_until=None,
    )
    await store.record_request(approval)


# ── 1. JSON 端点基本 200 + headers ──────────────────────────────────────────

def test_export_json_returns_200_with_attachment_header(
    client: TestClient,
) -> None:
    sid = _create_session(client)
    resp = client.get(f"/control-plane/sessions/{sid}/export.json")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert (
        f'session-{sid}.json' in resp.headers["content-disposition"]
    )
    body = resp.json()
    assert body["session"]["id"] == sid
    assert isinstance(body["events"], list)
    assert isinstance(body["approvals"], list)
    assert isinstance(body["file_changes"], list)


# ── 2. Markdown 端点 200 + headers ──────────────────────────────────────────

def test_export_md_returns_200_with_attachment_header(
    client: TestClient,
) -> None:
    sid = _create_session(client)
    resp = client.get(f"/control-plane/sessions/{sid}/export.md")
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers["content-type"]
    assert f"session-{sid}.md" in resp.headers["content-disposition"]
    text = resp.text
    assert text.startswith(f"# Session {sid}")


# ── 3. 不存在的 session 404 ──────────────────────────────────────────────────

def test_export_json_404_when_session_missing(client: TestClient) -> None:
    resp = client.get("/control-plane/sessions/sess_does_not_exist/export.json")
    assert resp.status_code == 404


def test_export_md_404_when_session_missing(client: TestClient) -> None:
    resp = client.get("/control-plane/sessions/sess_does_not_exist/export.md")
    assert resp.status_code == 404


# ── 4. JSON 内容包含三大块 ──────────────────────────────────────────────────

def test_export_json_includes_events_approvals_and_file_changes(
    client: TestClient, cp_app
) -> None:
    import asyncio

    sid = _create_session(client)
    store = _store(cp_app)
    asyncio.get_event_loop().run_until_complete(
        _seed_events_and_approvals(store, sid)
    )

    resp = client.get(f"/control-plane/sessions/{sid}/export.json")
    assert resp.status_code == 200
    data = resp.json()

    # 5 个我们写的 + 可能有 session.created 等系统事件
    assert data["stats"]["event_count"] >= 5
    types = [e["type"] for e in data["events"]]
    assert "turn.started" in types
    assert "assistant.message" in types
    assert types.count("file.changed") == 2

    # approval 在
    assert data["stats"]["approval_count"] == 1
    assert data["approvals"][0]["action_kind"] == "bash_run"
    assert data["approvals"][0]["decision"] == "denied"

    # file_changes 聚合：foo.py 只出现一次，且 operation 是 'edit'（后写覆盖）
    assert data["stats"]["file_change_count"] == 1
    assert len(data["file_changes"]) == 1
    assert data["file_changes"][0]["path"] == "foo.py"
    assert data["file_changes"][0]["operation"] == "edit"


# ── 5. Markdown 含必要 section ───────────────────────────────────────────────

def test_export_md_has_required_sections(
    client: TestClient, cp_app
) -> None:
    import asyncio

    sid = _create_session(client)
    store = _store(cp_app)
    asyncio.get_event_loop().run_until_complete(
        _seed_events_and_approvals(store, sid)
    )

    resp = client.get(f"/control-plane/sessions/{sid}/export.md")
    assert resp.status_code == 200
    md = resp.text
    for section in ("## Metadata", "## Timeline", "## Approvals", "## File Changes"):
        assert section in md, f"missing section: {section}"
    # 事件类型出现在 timeline
    assert "turn.started" in md
    assert "assistant.message" in md
    # approval 摘要
    assert "bash_run" in md
    assert "denied" in md
    # file change op
    assert "EDIT" in md or "CREATE" in md


# ── 6. 空 session 也能导出（无事件） ────────────────────────────────────────

def test_export_handles_empty_session(client: TestClient) -> None:
    sid = _create_session(client)

    resp = client.get(f"/control-plane/sessions/{sid}/export.json")
    assert resp.status_code == 200
    data = resp.json()
    # session 创建时 API 可能自带 session.created 事件，非0也无妨
    assert data["stats"]["approval_count"] == 0
    assert data["stats"]["file_change_count"] == 0

    md_resp = client.get(f"/control-plane/sessions/{sid}/export.md")
    assert md_resp.status_code == 200
    assert "_No approvals._" in md_resp.text
    assert "_No file changes._" in md_resp.text


# ── 7. 三反引号防御：payload 含 ``` 不破 markdown fence ────────────────────

def test_export_md_escapes_triple_backticks_in_payload(
    client: TestClient, cp_app
) -> None:
    import asyncio

    sid = _create_session(client)
    store = _store(cp_app)

    async def _seed() -> None:
        await store.append_event(
            EventRecord(
                id=0,
                session_id=sid,
                turn_id=None,
                type="assistant.message",
                payload={"text": "here is code: ```python\nprint(1)\n```"},
                created_at="",
            )
        )

    asyncio.get_event_loop().run_until_complete(_seed())

    resp = client.get(f"/control-plane/sessions/{sid}/export.md")
    assert resp.status_code == 200
    md = resp.text
    # payload 内的 ``` 应被替换，不应出现连续三个 backtick 在 fence 之外
    # 简单判定：原始字符串里的 ``` 不会原样出现（escape 后变 ``ʼ``）
    assert "``ʼ``" in md
