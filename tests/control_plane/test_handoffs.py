"""Tests for Provider handoff — V1.1 P1 Provider 转交.

覆盖：
  - POST /sessions/{sid}/handoff 成功 (transfer)
  - POST /sessions/{sid}/handoff 成功 (review)
  - POST 404 当 from session 不存在
  - POST 503 当目标 provider 未注册
  - GET /sessions/{sid}/handoffs 空 + 非空
  - GET /sessions/{sid}/handoffs 404 当 session 不存在
  - GET /handoffs/{hid} 200 + 404
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.control_plane.test_daemon_api import (  # noqa: F401
    MockRuntime,
    app,
    app_pair,
    cp_app,
    client,
    mock_runtime,
)


def _create_session(client: TestClient, runtime_kind: str = "claude") -> str:
    resp = client.post(
        "/control-plane/sessions",
        json={"runtime_kind": runtime_kind, "model": "claude-sonnet-4-6"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ── 1. transfer 成功 ────────────────────────────────────────────────────────

def test_handoff_transfer_returns_201(client: TestClient) -> None:
    sid = _create_session(client, runtime_kind="claude")
    resp = client.post(
        f"/control-plane/sessions/{sid}/handoff",
        json={
            "target_provider": "codex",
            "kind": "transfer",
            "context_messages": 5,
            "include_diff": False,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["from_provider"] == "claude"
    assert body["to_provider"] == "codex"
    assert body["kind"] == "transfer"
    assert body["handoff_id"].startswith("hdoff_")
    assert body["to_session_id"].startswith("sess_")


# ── 2. review 成功 ──────────────────────────────────────────────────────────

def test_handoff_review_returns_201(client: TestClient) -> None:
    sid = _create_session(client, runtime_kind="codex")
    resp = client.post(
        f"/control-plane/sessions/{sid}/handoff",
        json={
            "target_provider": "claude",
            "kind": "review",
            "context_messages": 3,
            "include_diff": False,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "review"
    assert body["from_provider"] == "codex"
    assert body["to_provider"] == "claude"


# ── 3. 来源 session 不存在 → 404 ─────────────────────────────────────────────

def test_handoff_404_when_from_session_missing(client: TestClient) -> None:
    resp = client.post(
        "/control-plane/sessions/sess_missing/handoff",
        json={"target_provider": "codex", "kind": "transfer"},
    )
    assert resp.status_code == 404


# ── 4. 目标 provider 未注册 → 503 ───────────────────────────────────────────

def test_handoff_503_when_target_provider_unregistered(
    client: TestClient, cp_app
) -> None:
    """schema 限制 target_provider ∈ {claude, codex}，
    要触发 "未注册" 503 必须把对应 runtime 从 registry 移除。"""
    sid = _create_session(client, runtime_kind="claude")
    state = cp_app.state.cp  # AppState
    # 模拟 codex 没注册：从 registry 删掉
    state.runtime_registry._runtimes.pop("codex", None)  # type: ignore[attr-defined]

    resp = client.post(
        f"/control-plane/sessions/{sid}/handoff",
        json={"target_provider": "codex", "kind": "transfer"},
    )
    assert resp.status_code == 503


# ── 5. list handoffs 空 ─────────────────────────────────────────────────────

def test_list_handoffs_returns_empty_initially(client: TestClient) -> None:
    sid = _create_session(client)
    resp = client.get(f"/control-plane/sessions/{sid}/handoffs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == sid
    assert body["handoffs"] == []


# ── 6. list handoffs 非空（创建 handoff 后） ───────────────────────────────

def test_list_handoffs_returns_record_after_creation(client: TestClient) -> None:
    sid = _create_session(client, runtime_kind="claude")
    create = client.post(
        f"/control-plane/sessions/{sid}/handoff",
        json={"target_provider": "codex", "kind": "transfer"},
    )
    assert create.status_code == 201
    handoff_id = create.json()["handoff_id"]
    to_sid = create.json()["to_session_id"]

    # from session 上能看到
    resp = client.get(f"/control-plane/sessions/{sid}/handoffs")
    assert resp.status_code == 200
    items = resp.json()["handoffs"]
    assert len(items) == 1
    assert items[0]["id"] == handoff_id

    # to session 上也能看到（双向索引）
    resp2 = client.get(f"/control-plane/sessions/{to_sid}/handoffs")
    assert resp2.status_code == 200
    items2 = resp2.json()["handoffs"]
    assert len(items2) == 1
    assert items2[0]["id"] == handoff_id


# ── 7. list handoffs 不存在 session → 404 ──────────────────────────────────

def test_list_handoffs_404_when_session_missing(client: TestClient) -> None:
    resp = client.get("/control-plane/sessions/sess_missing/handoffs")
    assert resp.status_code == 404


# ── 8. get handoff 200 + 404 ────────────────────────────────────────────────

def test_get_handoff_returns_record(client: TestClient) -> None:
    sid = _create_session(client)
    create = client.post(
        f"/control-plane/sessions/{sid}/handoff",
        json={"target_provider": "codex", "kind": "transfer"},
    )
    assert create.status_code == 201
    handoff_id = create.json()["handoff_id"]

    resp = client.get(f"/control-plane/handoffs/{handoff_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == handoff_id
    assert body["from_session_id"] == sid
    assert body["kind"] == "transfer"


def test_get_handoff_404_when_missing(client: TestClient) -> None:
    resp = client.get("/control-plane/handoffs/hdoff_missing")
    assert resp.status_code == 404
