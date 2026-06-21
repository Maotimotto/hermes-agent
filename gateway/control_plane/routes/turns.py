"""Turn management routes for the Daemon API control plane.

Endpoints:
  POST   /sessions/{id}/turns          — start a turn
  DELETE /sessions/{sid}/turns/{tid}    — interrupt a running turn
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from agent.control_plane.error_recovery import classify_runtime_error
from agent.control_plane.ids import new_turn_id
from agent.control_plane.runtimes.interface import TurnInput
from agent.control_plane.store import SessionStore
from gateway.control_plane.deps import (
    AppState,
    EventBus,
    RuntimeRegistry,
    get_app_state,
    get_event_bus,
    get_runtime_registry,
    get_store,
)
from gateway.control_plane.schemas import (
    TurnCreate,
    TurnInterruptResponse,
    TurnResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["turns"])

# 一个 turn 最多自动重试一次（P1 清单：「Turn 失败后的自动重试（最多 1 次）」）。
# 即首次失败若 retryable=True，再起一次；第二次仍失败则发 turn.failed 终止。
MAX_TURN_ATTEMPTS = 2

TERMINAL_EVENT_TYPES = {"turn.completed", "turn.failed", "turn.cancelled"}


def _backoff_for_code(code: str) -> int:
    """根据 runtime 上报的 error code 查默认退避时长（毫秒）。

    与 error_recovery._DEFAULT_RETRY_AFTER_MS 保持一致；用一个独立 helper
    是因为 runtime 已自行分类、上报了 code，路由层无需再 classify_runtime_error
    就能拿到退避时长。
    """
    from agent.control_plane.error_recovery import (
        DEFAULT_RETRY_AFTER_MS,
        ErrorCategory,
    )

    try:
        return DEFAULT_RETRY_AFTER_MS.get(ErrorCategory(code), 0)
    except ValueError:
        return 0


async def _insert_turn_record(store: SessionStore, turn_id: str, session_id: str, prompt: str) -> None:
    """Insert a row into the turns table so FK constraints on events.turn_id are satisfied."""
    now = datetime.now(timezone.utc).isoformat()
    await store.driver.execute(
        """INSERT INTO turns (id, session_id, prompt, status, started_at)
           VALUES (?, ?, ?, 'running', ?)""",
        (turn_id, session_id, prompt, now),
    )
    await store.driver.commit()


# ── POST /sessions/{session_id}/turns ────────────────────────────────────────


@router.post(
    "/sessions/{session_id}/turns",
    status_code=202,
    response_model=TurnResponse,
)
async def create_turn(
    session_id: str,
    body: TurnCreate,
    store: SessionStore = Depends(get_store),
    state: AppState = Depends(get_app_state),
) -> TurnResponse:
    """Start a new turn within a session.

    1. Validate session exists.
    2. Generate turn ID.
    3. Insert turn record into turns table (for FK constraint).
    4. Persist turn.started event.
    5. Start runtime turn in background task (event-driven).
    6. Return immediately with 202.
    """
    rec = await store.get_session(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    tid = new_turn_id()

    # Insert turn record for FK constraint
    await _insert_turn_record(store, tid, session_id, body.prompt)

    # NB: runtime's start_turn yields TurnStartedEvent itself; routes shouldn't
    # double-publish. If no runtime is registered we publish a synthetic one
    # below.

    # Determine runtime kind
    kind = state.runtime_registry.get_session_runtime(session_id) or rec.runtime_kind
    runtime = state.runtime_registry.get(kind)

    if runtime is not None:
        # Fire-and-forget background task to run the turn
        turn_input = TurnInput(
            prompt=body.prompt,
            system_prompt=body.system_prompt,
            context_files=body.context_files,
            metadata=body.metadata,
        )

        async def _run_turn() -> None:
            """运行 turn，遇可重试错误自动重试一次。

            重试触发条件（任一）：
              1) runtime 主动 yield TurnFailedEvent（claude/codex runtime 都走这条路径）
              2) start_turn() 异步迭代抛 Python 异常（路由层兜底）

            两种情况都用 ClassifiedError.retryable 判定；首次失败若 retryable
            则吞掉 turn.failed、补发 turn.retrying、退避后再起；重试再失败
            则把第二次的 turn.failed 透传上去终止。
            """
            attempt = 1
            while True:
                turn_failed_payload: dict[str, Any] | None = None
                python_exc: BaseException | None = None
                saw_terminal = False
                try:
                    async for event in runtime.start_turn(session_id, turn_input):
                        event_dict = event.model_dump(mode="json")
                        # Force IDs to control-plane values (runtime may have
                        # stashed provider-specific ids that don't match our
                        # turns.id FK).
                        event_dict["session_id"] = session_id
                        event_dict["turn_id"] = tid

                        # 拦截 turn.failed：不立即 publish，留给重试决策
                        if event.type == "turn.failed":
                            turn_failed_payload = event_dict
                            break

                        if event.type in TERMINAL_EVENT_TYPES:
                            saw_terminal = True
                        state.event_bus.publish(session_id, event_dict)
                except asyncio.CancelledError:
                    if not saw_terminal:
                        state.event_bus.publish(
                            session_id,
                            {
                                "type": "turn.cancelled",
                                "session_id": session_id,
                                "turn_id": tid,
                                "reason": "cancelled",
                            },
                        )
                    return
                except Exception as exc:  # noqa: BLE001
                    logger.exception("turn %s failed (attempt %d)", tid, attempt)
                    python_exc = exc

                # ── 决策：完成？重试？终止？─────────────────────
                if turn_failed_payload is None and python_exc is None:
                    # 正常结束（runtime 已 yield turn.completed），收工
                    return

                # 拿到一个分类结果（payload 优先；否则用 exception）
                if turn_failed_payload is not None:
                    code = turn_failed_payload.get("code") or ""
                    retryable = bool(turn_failed_payload.get("retryable"))
                    error_text = str(turn_failed_payload.get("error", ""))
                    # runtime 已设了 retryable=None 时回退到 classifier
                    if turn_failed_payload.get("retryable") is None and error_text:
                        cls = classify_runtime_error(
                            Exception(error_text), provider=kind,
                        )
                        retryable = cls.retryable
                        code = code or cls.code
                        backoff_ms = cls.retry_after_ms
                        # 用分类器的友好文案覆盖原始 error
                        if cls.friendly_message:
                            turn_failed_payload["error"] = cls.friendly_message
                            turn_failed_payload["code"] = cls.code
                            turn_failed_payload["retryable"] = cls.retryable
                    else:
                        backoff_ms = _backoff_for_code(code)
                else:
                    cls = classify_runtime_error(python_exc, provider=kind)
                    retryable = cls.retryable
                    code = cls.code
                    backoff_ms = cls.retry_after_ms
                    turn_failed_payload = {
                        "type": "turn.failed",
                        "session_id": session_id,
                        "turn_id": tid,
                        **cls.to_event_payload(),
                    }

                # 还能再试 → 发 turn.retrying，退避，下一轮 while
                if retryable and attempt < MAX_TURN_ATTEMPTS:
                    state.event_bus.publish(
                        session_id,
                        {
                            "type": "turn.retrying",
                            "session_id": session_id,
                            "turn_id": tid,
                            "attempt": attempt + 1,
                            "reason": code,
                            "backoff_ms": backoff_ms,
                        },
                    )
                    if backoff_ms > 0:
                        try:
                            await asyncio.sleep(backoff_ms / 1000)
                        except asyncio.CancelledError:
                            # 退避期间用户取消
                            state.event_bus.publish(
                                session_id,
                                {
                                    "type": "turn.cancelled",
                                    "session_id": session_id,
                                    "turn_id": tid,
                                    "reason": "cancelled",
                                },
                            )
                            return
                    attempt += 1
                    continue

                # 不重试 / 已达上限 → 透传 turn.failed
                state.event_bus.publish(session_id, turn_failed_payload)
                return

        async def _run_turn_with_cleanup() -> None:
            try:
                await _run_turn()
            finally:
                state.runtime_registry.remove_turn(tid)

        task = asyncio.create_task(_run_turn_with_cleanup())
        state.runtime_registry.register_turn(tid, session_id, task)
    else:
        # No runtime registered — emit synthetic turn.started so the WS
        # client can still see the turn lifecycle.
        logger.warning("No runtime registered for kind=%s; turn accepted but not executed", kind)
        state.event_bus.publish(
            session_id,
            {
                "type": "turn.started",
                "session_id": session_id,
                "turn_id": tid,
                "prompt": body.prompt,
            },
        )

    return TurnResponse(turn_id=tid, session_id=session_id, status="started")


# ── DELETE /sessions/{sid}/turns/{tid} ────────────────────────────────────────


@router.delete(
    "/sessions/{session_id}/turns/{turn_id}",
    response_model=TurnInterruptResponse,
)
async def interrupt_turn(
    session_id: str,
    turn_id: str,
    store: SessionStore = Depends(get_store),
    state: AppState = Depends(get_app_state),
) -> TurnInterruptResponse:
    """Interrupt a running turn.

    Cancels the background asyncio task and notifies the runtime.
    """
    # Validate session exists
    rec = await store.get_session(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    task = state.runtime_registry.get_turn_task(turn_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Turn not found or already finished: {turn_id}")

    # Try runtime interrupt
    kind = state.runtime_registry.get_session_runtime(session_id)
    if kind:
        runtime = state.runtime_registry.get(kind)
        if runtime:
            try:
                await runtime.interrupt_turn(turn_id)
            except Exception as exc:
                logger.warning("runtime interrupt failed: %s", exc)

    # Cancel the asyncio task
    if not task.done():
        task.cancel()

    # Publish — EventBus persistence hook will persist it.
    state.event_bus.publish(
        session_id,
        {
            "type": "turn.cancelled",
            "session_id": session_id,
            "turn_id": turn_id,
            "reason": "user_interrupt",
        },
    )

    return TurnInterruptResponse(turn_id=turn_id, status="interrupted")
