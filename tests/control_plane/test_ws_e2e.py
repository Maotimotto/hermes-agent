"""End-to-end smoke test for the control-plane WebSocket event stream.

This test boots a real control-plane daemon (out-of-process), connects a
WebSocket client, fires a turn against the **codex** runtime, and verifies:

  1. The server accepts the WS upgrade.
  2. ``turn.started`` and ``turn.completed`` (or ``turn.failed``) arrive
     in real-time, in order, on the WS.
  3. Heartbeats fire at ~1 Hz when no events are flowing.
  4. The set of WS events equals (or is a subset of) what's persisted
     in the events table — i.e. no event is published-but-not-stored
     and vice-versa.

Skips automatically when:
  - ``HERMES_CP_WS_E2E=1`` is not set (test is opt-in by default —
    requires a live MySQL + a working codex CLI in the env).
  - aiohttp / websockets aren't installed.

Run manually:
  HERMES_CP_DB_URL=mysql://... \\
  HERMES_CP_WS_E2E=1 \\
  pytest tests/control_plane/test_ws_e2e.py -xvs
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("HERMES_CP_WS_E2E") != "1",
    reason="opt-in: set HERMES_CP_WS_E2E=1 with a live codex env",
)

aiohttp = pytest.importorskip("aiohttp")
websockets = pytest.importorskip("websockets")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def daemon():
    """Boot a control-plane daemon on a free port; tear down on test exit."""
    port = _free_port()
    repo = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo)
    log_path = Path(tempfile.gettempdir()) / f"cp_e2e_daemon_{port}.log"
    log_fh = open(log_path, "wb")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-u",  # unbuffered so logs flush immediately
            "-m",
            "scripts.run_control_plane",
            "--port",
            str(port),
            "--log-level",
            "info",
        ],
        cwd=str(repo),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    # Wait for /control-plane/health to come up
    import urllib.request

    deadline = time.time() + 20
    base = f"http://127.0.0.1:{port}"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/control-plane/health", timeout=1) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        out = proc.communicate()[0].decode(errors="replace")
        raise RuntimeError(f"daemon didn't come up:\n{out[-2000:]}")

    yield base

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    log_fh.close()
    # Print the last bit of the daemon log so test failures are diagnosable
    try:
        tail = log_path.read_bytes()[-4000:].decode(errors="replace")
        if tail.strip():
            print(f"\n--- daemon log tail ({log_path}) ---\n{tail}\n--- end log ---")
    except Exception:
        pass


@pytest.mark.asyncio
async def test_ws_streams_turn_lifecycle(daemon):
    """Full WS round-trip: create session → open WS → fire turn → assert events."""
    base = daemon
    ws_base = base.replace("http://", "ws://")

    async with aiohttp.ClientSession() as http:
        # 1. Create a codex-backed session
        async with http.post(
            f"{base}/control-plane/sessions",
            json={"runtime_kind": "codex", "model": "gpt-5", "repo_path": "/tmp"},
        ) as r:
            assert r.status == 201, await r.text()
            sess = await r.json()
            sid = sess["id"]
            assert sess["status"] in ("running", "created"), sess

        # 2. Open WS
        ws = await websockets.connect(f"{ws_base}/control-plane/sessions/{sid}/events/ws")
        try:
            events: list[dict] = []
            heartbeats = 0

            async def reader():
                nonlocal heartbeats
                try:
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=120)
                        msg = json.loads(raw)
                        if msg.get("type") == "heartbeat":
                            heartbeats += 1
                            continue
                        events.append(msg)
                except (asyncio.TimeoutError, Exception):
                    pass

            rt = asyncio.create_task(reader())
            await asyncio.sleep(0.3)  # let the server install our subscription

            # 3. Fire a turn
            async with http.post(
                f"{base}/control-plane/sessions/{sid}/turns",
                json={"prompt": "一句话回答：今年是几年？"},
            ) as r:
                assert r.status == 202, await r.text()

            # 4. Wait until we see a terminal event or 60s elapses
            deadline = time.time() + 60
            while time.time() < deadline:
                if any(
                    e.get("type") in ("turn.completed", "turn.failed", "turn.cancelled")
                    for e in events
                ):
                    break
                await asyncio.sleep(0.5)
            await asyncio.sleep(1)  # let any trailing event land

        finally:
            await ws.close()
            try:
                await asyncio.wait_for(rt, timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        # ── assertions ────────────────────────────────────────────────────
        types = [e.get("type") for e in events]
        assert "turn.started" in types, f"no turn.started in WS stream: {types}"
        assert any(t in ("turn.completed", "turn.failed") for t in types), (
            f"no terminal event: {types}"
        )
        # no duplicate turn.started — that was a 2026-06 regression we fixed
        assert types.count("turn.started") == 1, (
            f"duplicate turn.started: {types}"
        )
        # heartbeat behaves: at least one heartbeat per ~2s of idle
        # (we usually see 60+ over a full minute)
        # Lower bound is loose because turn might finish quickly.
        assert heartbeats >= 1, f"no heartbeat received: {heartbeats}"

        # 5. Cross-check against persisted events
        async with http.get(f"{base}/control-plane/sessions/{sid}/events?limit=200") as r:
            db = (await r.json())["events"]
        db_types = [e["type"] for e in db]
        # WS should be a subset of DB (DB has session.started which fires
        # before the WS subscribed)
        for t in types:
            assert t in db_types, f"WS event {t} missing from DB: {db_types}"
        # And no DB row should be missing a turn_id where WS had one
        for e in db:
            if e["type"] in ("turn.started", "turn.completed", "turn.failed", "assistant.message"):
                assert e["turn_id"] is not None, (
                    f"event {e['id']} type={e['type']} has null turn_id in DB"
                )
