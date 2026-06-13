"""CodexAppServerRuntime — AgentRuntime 适配器，委托现有三件套。

三件套（不做任何修改）：
  - agent/transports/codex_app_server.py       → CodexAppServerClient（JSON-RPC 客户端 + 子进程管理）
  - agent/transports/codex_app_server_session.py → CodexAppServerSession（session 生命周期 + turn 管理）
  - agent/transports/codex_event_projector.py    → CodexEventProjector（事件投影）

本文件职责：
  1. 接口适配：AgentRuntime ABC 方法 → 内部调用三件套
  2. 事件映射：Codex 原始 notification → control_plane.hermes_event（HermesEvent）
  3. 补充映射：assistant.thinking / file.changed 等 codex_event_projector 中缺失的类型
  4. thread_id 持久化到 SessionStore

设计原则：
  - 委托模式（composition），不是继承
  - 不重写 JSON-RPC 客户端、不重写子进程管理
  - 不动三件套任何一个文件
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import Any, AsyncIterator, Literal, Optional

from agent.control_plane.hermes_event import (
    ApprovalRequestedEvent,
    AssistantDeltaEvent,
    AssistantMessageEvent,
    AssistantThinkingEvent,
    FileChangedEvent,
    HermesEvent,
    SessionStartedEvent,
    ToolCompletedEvent,
    ToolOutputEvent,
    ToolStartedEvent,
    TurnCancelledEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)
from agent.control_plane.ids import new_turn_id
from agent.control_plane.store import SessionStore
from agent.transports.codex_app_server import CodexAppServerClient, check_codex_binary
from agent.transports.codex_app_server_session import CodexAppServerSession

from .interface import (
    AgentRuntime,
    ApprovalDecision,
    HealthStatus,
    RuntimeConfig,
    RuntimeKind,
    SessionRef,
    StartSessionInput,
    TurnInput,
)

logger = logging.getLogger(__name__)


# ─── 事件映射补充层 ──────────────────────────────────────────
#
# codex_event_projector.py 输出的是 ProjectionResult（面向 messages 列表），
# 不是 HermesEvent。此处实现 notification → HermesEvent 的独立映射。
#
# 补充映射（codex_event_projector 中缺失的类型）：
#   - assistant.thinking ← item/completed reasoning
#   - file.changed       ← item/completed fileChange
#   - assistant.delta    ← item/*/outputDelta（projector 忽略 delta）


def _map_codex_notification_to_hermes_event(
    notification: dict[str, Any],
    session_id: str,
    turn_id: str,
    seq: int,
) -> HermesEvent | None:
    """将 Codex 原始 notification 映射为 HermesEvent。

    补充映射层 — 不动 codex_event_projector，在 runtime 内部完成。
    对于 codex_event_projector 已覆盖的类型（tool.started、
    tool.completed、assistant.message 等），此处也做映射以生成
    HermesEvent 流，两套投影互不干扰。
    """
    method: str = notification.get("method", "")
    params: dict[str, Any] = notification.get("params", {}) or {}
    item: dict[str, Any] = params.get("item") or {}
    item_type: str = item.get("type", "")
    item_id: str = item.get("id", "")

    # ── item/started → ToolStartedEvent ─────────────────────
    if method == "item/started":
        if item_type in {
            "commandExecution",
            "fileChange",
            "mcpToolCall",
            "dynamicToolCall",
        }:
            return ToolStartedEvent(
                session_id=session_id,
                turn_id=turn_id,
                tool_call_id=item_id,
                tool_name=_resolve_tool_name(item_type, item),
                input=_extract_tool_input(item_type, item),
                seq=seq,
            )
        return None

    # ── item/completed → 各类 HermesEvent ────────────────────
    if method == "item/completed":
        if item_type == "agentMessage":
            return AssistantMessageEvent(
                session_id=session_id,
                turn_id=turn_id,
                text=item.get("text", ""),
                seq=seq,
            )

        # ★ 补充映射：assistant.thinking（codex_event_projector 缺失）
        if item_type == "reasoning":
            # reasoning item 可能同时有 summary + content，合并
            parts: list[str] = []
            for key in ("summary", "content"):
                for p in item.get(key) or []:
                    parts.append(str(p))
            text = "\n".join(parts) if parts else ""
            return AssistantThinkingEvent(
                session_id=session_id,
                turn_id=turn_id,
                text=text,
                seq=seq,
            )

        if item_type in {"commandExecution", "mcpToolCall", "dynamicToolCall"}:
            exit_code = item.get("exitCode")
            status: Literal["ok", "error"] = (
                "error" if exit_code is not None and exit_code != 0 else "ok"
            )
            error_msg = None
            if status == "error":
                error_msg = (
                    f"exit code {exit_code}: "
                    + (item.get("aggregatedOutput") or "")[:500]
                )
            return ToolCompletedEvent(
                session_id=session_id,
                turn_id=turn_id,
                tool_call_id=item_id,
                status=status,
                error=error_msg,
                seq=seq,
            )

        # ★ 补充映射：file.changed（codex_event_projector 缺失）
        if item_type == "fileChange":
            changes = item.get("changes") or []
            if changes:
                first = changes[0]
                path = first.get("path", "")
                kind = (first.get("kind") or {}).get("type", "update")
            else:
                path = ""
                kind = "update"
            return FileChangedEvent(
                session_id=session_id,
                turn_id=turn_id,
                path=path,
                operation=_map_change_kind(kind),
                seq=seq,
            )

        return None

    # ── outputDelta → AssistantDeltaEvent / ToolOutputEvent ──
    # 通知格式示例：method="item/agentMessage/outputDelta"
    if "outputDelta" in method or "/delta" in method:
        delta = params.get("delta") or {}
        text = delta.get("text", "")
        if not text:
            return None
        # 从 method 推断 item_type（如 "item/agentMessage/outputDelta" → "agentMessage"）
        parts = method.split("/")
        inferred_type = parts[1] if len(parts) >= 3 else ""
        if inferred_type == "agentMessage":
            return AssistantDeltaEvent(
                session_id=session_id,
                turn_id=turn_id,
                text=text,
                seq=seq,
            )
        # 工具输出增量
        if inferred_type in {
            "commandExecution",
            "mcpToolCall",
            "dynamicToolCall",
        }:
            return ToolOutputEvent(
                session_id=session_id,
                turn_id=turn_id,
                tool_call_id=item_id or "",
                text=text,
                seq=seq,
            )
        return None

    # ── turn/completed → Turn 终结事件 ───────────────────────
    if method == "turn/completed":
        turn_obj = params.get("turn") or {}
        turn_status = turn_obj.get("status", "completed")
        if turn_status in ("completed",):
            return TurnCompletedEvent(
                session_id=session_id,
                turn_id=turn_id,
                seq=seq,
            )
        if turn_status in ("failed",):
            error_text = ""
            err_obj = turn_obj.get("error")
            if isinstance(err_obj, dict):
                error_text = err_obj.get("message", str(err_obj))
            elif err_obj:
                error_text = str(err_obj)
            return TurnFailedEvent(
                session_id=session_id,
                turn_id=turn_id,
                error=error_text,
                seq=seq,
            )
        if turn_status in ("interrupted",):
            return TurnCancelledEvent(
                session_id=session_id,
                turn_id=turn_id,
                seq=seq,
            )
        # 未知 status → 视为 failed
        return TurnFailedEvent(
            session_id=session_id,
            turn_id=turn_id,
            error=f"unexpected turn status: {turn_status}",
            seq=seq,
        )

    # ── 未识别通知 → 忽略 ───────────────────────────────────
    return None


def _resolve_tool_name(item_type: str, item: dict[str, Any]) -> str:
    """从 Codex item 推断工具名称。"""
    if item_type == "commandExecution":
        return "exec_command"
    if item_type == "fileChange":
        return "apply_patch"
    if item_type == "mcpToolCall":
        server = item.get("server", "mcp")
        tool = item.get("tool", "unknown")
        return f"mcp.{server}.{tool}"
    if item_type == "dynamicToolCall":
        return item.get("tool", "unknown")
    return item_type


def _extract_tool_input(item_type: str, item: dict[str, Any]) -> Any:
    """从 Codex item 提取工具输入。"""
    if item_type == "commandExecution":
        return {"command": item.get("command", ""), "cwd": item.get("cwd", "")}
    if item_type == "fileChange":
        return {"changes": item.get("changes", [])}
    if item_type in ("mcpToolCall", "dynamicToolCall"):
        return item.get("arguments", {})
    return None


def _map_change_kind(codex_kind: str) -> Literal["create", "edit", "delete"]:
    """将 Codex fileChange kind 映射到 HermesEvent operation。"""
    mapping = {
        "add": "create",
        "create": "create",
        "update": "edit",
        "edit": "edit",
        "delete": "delete",
        "remove": "delete",
    }
    return mapping.get(codex_kind.lower(), "edit")


# ─── CodexAppServerRuntime ────────────────────────────────────


class CodexAppServerRuntime(AgentRuntime):
    """AgentRuntime 适配器 — 组合现有三件套。

    【委托调用关系】
      - start_session → 委托 CodexAppServerSession.ensure_started()
                         （内部调用 CodexAppServerClient.initialize + request("thread/start")）
      - start_turn    → 委托 CodexAppServerSession.run_turn()
                         （内部调用 CodexAppServerClient.request("turn/start") + 通知循环）
      - interrupt_turn → 委托 CodexAppServerSession.request_interrupt()
                          （内部调用 CodexAppServerClient.request("turn/interrupt")）
      - health_check  → 委托 check_codex_binary()

    【补充层】
      - notification → HermesEvent 映射由 _map_codex_notification_to_hermes_event() 完成
      - 不动 codex_event_projector.py
    """

    @property
    def provider(self) -> Literal["codex"]:
        return "codex"

    def __init__(
        self,
        store: SessionStore,
        *,
        codex_bin: str = "codex",
        codex_home: str | None = None,
        permission_profile: str | None = None,
    ) -> None:
        self._store = store
        self._codex_bin = codex_bin
        self._codex_home = codex_home
        self._permission_profile = permission_profile
        # session_id (provider_session_id) → CodexAppServerSession
        self._sessions: dict[str, CodexAppServerSession] = {}
        # turn_id → session_id（用于 interrupt_turn 按 turn_id 查找 session）
        self._turn_to_session: dict[str, str] = {}
        self._lock = threading.Lock()

    # ── 生命周期 ──────────────────────────────────────────────

    async def start_session(self, input: StartSessionInput) -> SessionRef:
        """启动一个新 session。

        【委托】CodexAppServerSession.ensure_started()
          → 内部创建 CodexAppServerClient，执行 initialize 握手 + thread/start
        """
        # 委托：创建 session 对象
        session = CodexAppServerSession(
            cwd=input.repo_path,
            codex_bin=self._codex_bin,
            codex_home=self._codex_home,
            permission_profile=self._permission_profile,
        )

        # 委托：spawn 子进程 + initialize + thread/start
        loop = asyncio.get_event_loop()
        thread_id: str = await loop.run_in_executor(
            None, session.ensure_started
        )

        # 构造 SessionRef
        ref = SessionRef(
            hermes_session_id="",  # 由调用方填入
            provider_session_id=thread_id,
            provider="codex",
        )

        # 缓存 session
        with self._lock:
            self._sessions[thread_id] = session

        logger.info(
            "CodexAppServerRuntime: session started, thread_id=%s cwd=%s",
            thread_id[:8],
            input.repo_path,
        )
        return ref

    async def start_turn(
        self,
        session_id: str,
        input: TurnInput,
        signal: object | None = None,
    ) -> AsyncIterator[HermesEvent]:
        """在 session 中启动一个 turn。

        【委托】CodexAppServerSession.run_turn()
          → 内部调用 CodexAppServerClient.request("turn/start") + 通知循环
        【补充层】通过 on_event 回调捕获原始 notification，映射为 HermesEvent
        """
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise RuntimeError(
                f"CodexAppServerRuntime: no session for {session_id!r}"
            )

        # ── 设置 on_event 回调，桥接 sync → async ──
        async_queue: asyncio.Queue[HermesEvent | None] = asyncio.Queue()
        loop = asyncio.get_event_loop()
        seq_counter = {"value": 0}
        terminal_emitted = {"value": False}
        captured_turn_id = {"value": ""}

        def _on_event(notification: dict[str, Any]) -> None:
            """【补充层】在三件套 on_event 回调中捕获原始通知并映射为 HermesEvent。

            这段代码运行在 executor 线程中，通过 call_soon_threadsafe
            将映射结果投递到 event loop 的 async_queue。
            """
            seq_counter["value"] += 1
            # 尝试从通知中提取 turn_id
            turn_obj = (notification.get("params", {}) or {}).get("turn") or {}
            tid = turn_obj.get("id") or captured_turn_id["value"]

            event = _map_codex_notification_to_hermes_event(
                notification,
                session_id=session_id,
                turn_id=tid,
                seq=seq_counter["value"],
            )
            if event is not None:
                # 记录是否已发出终端事件
                if isinstance(
                    event,
                    (TurnCompletedEvent, TurnFailedEvent, TurnCancelledEvent),
                ):
                    terminal_emitted["value"] = True
                loop.call_soon_threadsafe(async_queue.put_nowait, event)

        # 委托：设置回调
        session._on_event = _on_event

        def _run_turn_sync() -> Any:
            """在线程池中运行 run_turn，结束后发送哨兵。"""
            try:
                result = session.run_turn(input.prompt)
                # 记录 turn_id
                captured_turn_id["value"] = result.turn_id or ""
                return result
            finally:
                # 哨兵：通知 async 迭代器 run_turn 已结束
                loop.call_soon_threadsafe(async_queue.put_nowait, None)

        # ── 在线程池启动 turn ──
        future = loop.run_in_executor(None, _run_turn_sync)

        # ── yield turn.started ──
        yield TurnStartedEvent(
            session_id=session_id,
            turn_id="",
            seq=0,
        )

        # ── 从 async_queue 读取并 yield 事件 ──
        while True:
            event = await async_queue.get()
            if event is None:
                break
            # 更新 captured_turn_id
            if hasattr(event, "turn_id") and event.turn_id:
                captured_turn_id["value"] = event.turn_id
            yield event

        # ── 等待 run_turn 完成 ──
        result = await future

        # 如果 on_event 回调未发出终端事件（如超时、turn_aborted），
        # 根据 TurnResult 补发终端事件
        if not terminal_emitted["value"]:
            tid = captured_turn_id["value"] or result.turn_id or ""
            if result.error:
                yield TurnFailedEvent(
                    session_id=session_id,
                    turn_id=tid,
                    error=result.error,
                )
            elif result.interrupted:
                yield TurnCancelledEvent(
                    session_id=session_id,
                    turn_id=tid,
                    reason="interrupted",
                )
            else:
                yield TurnCompletedEvent(
                    session_id=session_id,
                    turn_id=tid,
                )

    async def resume_session(self, provider_session_id: str) -> SessionRef:
        """恢复一个已存在的 provider session。

        当前实现：构造 SessionRef，不重建子进程（session 由外部传入）。
        V1.0.0 简化实现，未来可通过 store 查找历史 session。
        """
        return SessionRef(
            hermes_session_id="",
            provider_session_id=provider_session_id,
            provider="codex",
        )

    async def steer_turn(self, turn_id: str, input: str) -> None:
        """向运行中的 turn 注入额外指令。

        Codex app-server 协议当前不支持 steer（静默忽略）。
        """
        logger.debug(
            "CodexAppServerRuntime: steer_turn not supported, ignoring "
            "turn_id=%s",
            turn_id,
        )

    async def interrupt_turn(self, turn_id: str) -> None:
        """中断运行中的 turn。

        【委托】CodexAppServerSession.request_interrupt()
          → 内部设置 interrupt_event，run_turn 循环检测到后发 turn/interrupt
        """
        with self._lock:
            session_id = self._turn_to_session.get(turn_id)
            session = (
                self._sessions.get(session_id) if session_id else None
            )
            # 如果没有 turn → session 映射，遍历所有 session
            if session is None:
                for s in self._sessions.values():
                    session = s
                    break
        if session is not None:
            # 委托：request_interrupt
            session.request_interrupt()

    # ── 审批 ──────────────────────────────────────────────────

    async def resolve_approval(self, decision: ApprovalDecision) -> None:
        """将 Hermes 审批决策回传给 provider。

        Codex 的审批由 CodexAppServerSession.run_turn() 内部的
        approval_callback 驱动。此方法供外部 Approval Gate 调用，
        V1.0.0 为透传预留接口。
        """
        logger.debug(
            "CodexAppServerRuntime: resolve_approval called, "
            "request_id=%s decision=%s",
            decision.request_id,
            decision.decision,
        )

    # ── 健康检查 ──────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        """检查 provider 是否可用。

        【委托】check_codex_binary() — 验证 codex CLI 安装且版本满足最低要求。
        """
        ok, msg = check_codex_binary(self._codex_bin)
        return HealthStatus(available=ok, message=msg)

    # ── 内部工具 ──────────────────────────────────────────────

    def _get_session(self, session_id: str) -> CodexAppServerSession | None:
        """按 provider_session_id 获取 session。"""
        with self._lock:
            return self._sessions.get(session_id)

    def close(self) -> None:
        """关闭所有 session（清理子进程）。"""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._turn_to_session.clear()
        for s in sessions:
            try:
                s.close()
            except Exception:  # pragma: no cover - best-effort
                pass
