"""ClaudeAgentSdkRuntime — AgentRuntime 实现，包装 claude-agent-sdk 子进程。

SDK 实质（ADR-001 实测）：
  - pip 包 claude-agent-sdk==0.2.101，wheel 76MB，bundle 一个 claude ELF 二进制
  - Python ClaudeSDKClient.query() 内部 spawn claude 子进程，stdio 通信
  - ClaudeAgentOptions.env: dict[str, str] 支持自定义环境变量注入

认证模式 B（Hermes config 驱动）：
  - hermes config.providers.claude 读取 base_url / api_key / model
  - 启动 SDK 子进程时注入为 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_MODEL
  - 不依赖 ~/.claude/.credentials.json 或 shell 全局环境变量

事件流：
  SDK message stream → _map_sdk_message_to_hermes_event() → AsyncIterator[HermesEvent]
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Literal

from agent.control_plane.hermes_event import (
    AssistantDeltaEvent,
    AssistantMessageEvent,
    AssistantThinkingEvent,
    HermesEvent,
    SessionStartedEvent,
    ToolCompletedEvent,
    ToolStartedEvent,
    TurnCancelledEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)
from agent.control_plane.ids import new_turn_id

from .interface import (
    AgentRuntime,
    ApprovalDecision,
    HealthStatus,
    SessionRef,
    StartSessionInput,
    TurnInput,
)

logger = logging.getLogger(__name__)


# ─── SDK 延迟导入 ─────────────────────────────────────────────
#
# claude-agent-sdk 是可选依赖（extras [control-plane]，76MB wheel），
# 可能未安装。延迟到运行时才导入，保证 import 本模块不报错。


def _import_sdk() -> tuple[type, type]:
    """延迟导入 claude-agent-sdk，返回 (ClaudeSDKClient, ClaudeAgentOptions)。

    Raises:
        ImportError: SDK 未安装时抛出。
    """
    try:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient  # type: ignore[import-untyped]

        return ClaudeSDKClient, ClaudeAgentOptions
    except ImportError:
        raise ImportError(
            "claude-agent-sdk is required for ClaudeAgentSdkRuntime. "
            "Install with: pip install hermes-agent[control-plane]"
        )


# ─── 环境变量构建（认证注入） ─────────────────────────────────
#
# [ENV_INJECTION] 认证注入位点
# hermes config (base_url / api_key / model)
#   → env dict (ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_MODEL)
#   → ClaudeAgentOptions.env → 子进程
#
# 三项角色：
#   ANTHROPIC_AUTH_TOKEN (必需) — 认证 token
#   ANTHROPIC_MODEL      (必需) — 模型名，如 claude-opus-4-6
#   ANTHROPIC_BASE_URL   (可选) — 中转商 URL，官方 Anthropic 场景可不传

_REQUIRED_CONFIG_KEYS: list[str] = ["api_key", "model"]
_OPTIONAL_CONFIG_KEYS: list[str] = ["base_url"]

# config key → 环境变量名
_CONFIG_TO_ENV: dict[str, str] = {
    "api_key": "ANTHROPIC_AUTH_TOKEN",
    "model": "ANTHROPIC_MODEL",
    "base_url": "ANTHROPIC_BASE_URL",
}


def build_env_from_config(config: dict[str, Any]) -> dict[str, str]:
    """从 hermes config 构建子进程 env dict。

    Args:
        config: 包含 base_url / api_key / model 的配置字典。
                通常来自 hermes config.providers.claude。

    Returns:
        env dict，键为 ANTHROPIC_* 大写下划线变量名。

    Raises:
        ValueError: 必需字段（api_key 或 model）缺失时。
    """
    env: dict[str, str] = {}

    # 可选字段
    for config_key in _OPTIONAL_CONFIG_KEYS:
        value = config.get(config_key)
        if value:
            env[_CONFIG_TO_ENV[config_key]] = str(value)

    # 必需字段
    missing: list[str] = []
    for config_key in _REQUIRED_CONFIG_KEYS:
        value = config.get(config_key)
        if not value:
            missing.append(config_key)
        else:
            env[_CONFIG_TO_ENV[config_key]] = str(value)

    if missing:
        raise ValueError(
            f"Missing required config fields for Claude runtime: {', '.join(missing)}"
        )

    return env


def _mask_token(value: str) -> str:
    """[TOKEN_DESENSITIZE] token 脱敏位点：只保留前 6 字符 + ***。"""
    if len(value) <= 6:
        return "***"
    return value[:6] + "***"


# ─── SDK 消息 → HermesEvent 映射 ─────────────────────────────
#
# 实测事件序列（cpass.cc, 2026-06-13）：
#   6×SystemMessage(init/setup)
#     → AssistantMessage(ThinkingBlock)
#     → AssistantMessage(text block)
#     → ResultMessage(turn completed)
#
# 映射规则：
#   SystemMessage(init)              → session.started
#   SystemMessage(非 init)           → 忽略
#   AssistantMessage + ThinkingBlock → assistant.thinking ★ ADR-001 补充
#   AssistantMessage + TextBlock     → assistant.delta
#   AssistantMessage + ToolUseBlock  → tool.started
#   UserMessage   + ToolResultBlock  → tool.completed
#   ResultMessage                    → turn.completed


def _map_sdk_message_to_hermes_event(
    msg: object,
    session_id: str,
    turn_id: str,
    seq: int,
) -> list[HermesEvent]:
    """将 claude-agent-sdk 消息映射为 HermesEvent 列表。"""
    msg_type = type(msg).__name__

    # ── SystemMessage ───────────────────────────────────────
    if msg_type == "SystemMessage":
        subtype = getattr(msg, "subtype", "")
        if subtype == "init":
            # 从 SystemMessage(init) 提取 provider_session_id
            return [
                SessionStartedEvent(
                    session_id=session_id,
                    turn_id=turn_id,
                    seq=seq,
                    provider="claude",
                    provider_session_id=getattr(msg, "session_id", None),
                )
            ]
        # 6×SystemMessage 中只有第一个 init 有意义，其余忽略
        return []

    # ── AssistantMessage ────────────────────────────────────
    if msg_type == "AssistantMessage":
        events: list[HermesEvent] = []
        content = getattr(msg, "content", []) or []
        for block in content:
            block_type = type(block).__name__

            if block_type == "ThinkingBlock":
                # ★ assistant.thinking — ADR-001 实测发现，设计文档 5.4 漏列
                events.append(
                    AssistantThinkingEvent(
                        session_id=session_id,
                        turn_id=turn_id,
                        seq=seq,
                        text=getattr(block, "thinking", ""),
                    )
                )

            elif block_type == "TextBlock":
                events.append(
                    AssistantDeltaEvent(
                        session_id=session_id,
                        turn_id=turn_id,
                        seq=seq,
                        text=getattr(block, "text", ""),
                    )
                )

            elif block_type == "ToolUseBlock":
                events.append(
                    ToolStartedEvent(
                        session_id=session_id,
                        turn_id=turn_id,
                        seq=seq,
                        tool_call_id=getattr(block, "id", ""),
                        tool_name=getattr(block, "name", ""),
                        input=getattr(block, "input", None),
                    )
                )

        return events

    # ── UserMessage（含 ToolResultBlock）────────────────────
    if msg_type == "UserMessage":
        events = []
        content = getattr(msg, "content", []) or []
        for block in content:
            block_type = type(block).__name__
            if block_type == "ToolResultBlock":
                is_error = getattr(block, "is_error", False)
                events.append(
                    ToolCompletedEvent(
                        session_id=session_id,
                        turn_id=turn_id,
                        seq=seq,
                        tool_call_id=getattr(block, "tool_use_id", ""),
                        status="error" if is_error else "ok",
                    )
                )
        return events

    # ── ResultMessage ───────────────────────────────────────
    if msg_type == "ResultMessage":
        return [
            TurnCompletedEvent(
                session_id=session_id,
                turn_id=turn_id,
                seq=seq,
                summary=getattr(msg, "result", None),
            )
        ]

    # 未识别消息类型 → 忽略
    return []


# ─── ClaudeAgentSdkRuntime ────────────────────────────────────


class ClaudeAgentSdkRuntime(AgentRuntime):
    """AgentRuntime 实现 — 包装 claude-agent-sdk 子进程。

    认证：模式 B（Hermes config 驱动）
      hermes config → build_env_from_config() → ClaudeAgentOptions.env → 子进程

    生命周期：
      start_session → 预建 SessionRef（SDK session 在首次 query 隐式创建）
      start_turn    → ClaudeSDKClient.query() + receive_response() → HermesEvent
      interrupt_turn → asyncio.Event 通知终止
      health_check  → 校验 config 完整性 + SDK 可用性
    """

    @property
    def provider(self) -> Literal["claude"]:
        return "claude"

    def __init__(
        self,
        config: dict[str, Any],
    ) -> None:
        """
        Args:
            config: hermes config.providers.claude 配置字典。
                    必须包含 api_key 和 model，可选 base_url。
        """
        self._config = config
        # hermes_session_id → provider_session_id
        self._sessions: dict[str, str] = {}
        # turn_id → asyncio.Event（用于 interrupt_turn）
        self._abort_events: dict[str, asyncio.Event] = {}

    # ── 生命周期 ──────────────────────────────────────────────

    async def start_session(self, input: StartSessionInput) -> SessionRef:
        """启动一个新 session。

        Claude SDK 的 session 在首次 query() 时隐式创建。
        此处预构造 SessionRef，provider_session_id 在首次 turn 时
        从 SystemMessage(init) 提取后回填。
        """
        from agent.control_plane.ids import new_session_id

        hermes_id = new_session_id()
        self._sessions[hermes_id] = ""  # 占位，首次 turn 后回填

        logger.info(
            "ClaudeAgentSdkRuntime: session created, hermes_id=%s cwd=%s",
            hermes_id[:12],
            input.repo_path,
        )

        return SessionRef(
            hermes_session_id=hermes_id,
            provider_session_id="",  # 首次 turn 后填充
            provider="claude",
        )

    async def start_turn(
        self,
        session_id: str,
        input: TurnInput,
        signal: object | None = None,
    ) -> AsyncIterator[HermesEvent]:
        """在 session 中启动一个 turn。

        流程：
          1. 构建认证 env（[ENV_INJECTION] 位点）
          2. 创建 ClaudeAgentOptions
          3. ClaudeSDKClient context manager
          4. client.query(prompt) → client.receive_response() 遍历
          5. 每条 SDK 消息经 _map_sdk_message_to_hermes_event 映射后 yield
        """
        turn_id = new_turn_id()
        seq = 0

        # yield turn.started
        yield TurnStartedEvent(
            session_id=session_id,
            turn_id=turn_id,
            seq=seq,
        )
        seq += 1

        # ── [ENV_INJECTION] 构建认证 env ──────────────────
        try:
            env = build_env_from_config(self._config)
        except ValueError as exc:
            yield TurnFailedEvent(
                session_id=session_id,
                turn_id=turn_id,
                error=str(exc),
                seq=seq,
            )
            return

        # ── 延迟导入 SDK ─────────────────────────────────
        try:
            ClaudeSDKClient, ClaudeAgentOptions = _import_sdk()
        except ImportError as exc:
            yield TurnFailedEvent(
                session_id=session_id,
                turn_id=turn_id,
                error=str(exc),
                seq=seq,
            )
            return

        # ── 注册 abort event ──────────────────────────────
        abort_event = asyncio.Event()
        self._abort_events[turn_id] = abort_event

        try:
            # ── [TOKEN_DESENSITIZE] 日志脱敏位点 ─────────
            token = env.get("ANTHROPIC_AUTH_TOKEN", "")
            logger.debug(
                "ClaudeAgentSdkRuntime: starting turn_id=%s, "
                "ANTHROPIC_AUTH_TOKEN=%s, ANTHROPIC_BASE_URL=%s, "
                "ANTHROPIC_MODEL=%s",
                turn_id,
                _mask_token(token),  # ← token 脱敏，永远不打完整 token
                env.get("ANTHROPIC_BASE_URL", "(official)"),
                env.get("ANTHROPIC_MODEL", "(default)"),
            )

            # ── 构建 SDK options ──────────────────────────
            max_turns = 25
            worktree_path = None
            if input.metadata:
                max_turns = int(input.metadata.get("max_turns", 25))
                worktree_path = input.metadata.get("worktree_path")

            options = ClaudeAgentOptions(
                env=env,
                max_turns=max_turns,
                cwd=worktree_path,
            )

            # ── SDK 调用 ─────────────────────────────────
            terminal_yielded = False
            async with ClaudeSDKClient(options=options) as client:
                await client.query(input.prompt)

                async for msg in client.receive_response():
                    # 检查中断信号
                    if abort_event.is_set():
                        yield TurnCancelledEvent(
                            session_id=session_id,
                            turn_id=turn_id,
                            seq=seq,
                            reason="user_interrupt",
                        )
                        terminal_yielded = True
                        return

                    # 映射 SDK 消息 → HermesEvent
                    events = _map_sdk_message_to_hermes_event(
                        msg, session_id, turn_id, seq,
                    )

                    for event in events:
                        # 回填 provider_session_id
                        if (
                            isinstance(event, SessionStartedEvent)
                            and event.provider_session_id
                        ):
                            self._sessions[session_id] = event.provider_session_id

                        # 记录终端事件
                        if isinstance(
                            event,
                            (TurnCompletedEvent, TurnFailedEvent, TurnCancelledEvent),
                        ):
                            terminal_yielded = True

                        yield event
                        seq += 1

            # SDK 迭代结束但未产出终端事件 → 补发 turn.completed
            if not terminal_yielded:
                yield TurnCompletedEvent(
                    session_id=session_id,
                    turn_id=turn_id,
                    seq=seq,
                )

        except Exception as exc:
            # 子进程异常退出 → 产出 error 事件
            logger.error(
                "ClaudeAgentSdkRuntime: turn failed, turn_id=%s error=%s",
                turn_id,
                str(exc)[:200],
            )
            yield TurnFailedEvent(
                session_id=session_id,
                turn_id=turn_id,
                error=str(exc)[:500],
                seq=seq,
            )

        finally:
            self._abort_events.pop(turn_id, None)

    async def resume_session(self, provider_session_id: str) -> SessionRef:
        """恢复一个已存在的 provider session。

        Claude SDK 支持 resume(session_id) 恢复已有会话。
        V1.0.0 简化实现：构造 SessionRef，SDK 在首次 query 时自动 resume。
        """
        return SessionRef(
            hermes_session_id="",
            provider_session_id=provider_session_id,
            provider="claude",
        )

    async def steer_turn(self, turn_id: str, input: str) -> None:
        """向运行中的 turn 注入额外指令。

        Claude SDK 当前不支持 steer（静默忽略）。
        """
        logger.debug(
            "ClaudeAgentSdkRuntime: steer_turn not supported, ignoring turn_id=%s",
            turn_id,
        )

    async def interrupt_turn(self, turn_id: str) -> None:
        """中断运行中的 turn。

        通过 asyncio.Event 通知 start_turn 迭代器终止。
        """
        abort_event = self._abort_events.get(turn_id)
        if abort_event:
            abort_event.set()
            logger.info(
                "ClaudeAgentSdkRuntime: interrupt requested, turn_id=%s",
                turn_id,
            )

    # ── 审批 ──────────────────────────────────────────────────

    async def resolve_approval(self, decision: ApprovalDecision) -> None:
        """将 Hermes 审批决策回传给 provider。

        V1.0.0：透传预留接口。SDK 的 can_use_tool 回调在 future 版本接入。
        """
        logger.debug(
            "ClaudeAgentSdkRuntime: resolve_approval called, "
            "request_id=%s decision=%s",
            decision.request_id,
            decision.decision,
        )

    # ── 健康检查 ──────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        """检查 provider 是否可用。

        检查项：
          1. config 必需字段完整性（api_key / model）
          2. SDK 可用性（import 检查）
        """
        # 检查 config 完整性
        missing: list[str] = []
        for config_key in _REQUIRED_CONFIG_KEYS:
            if not self._config.get(config_key):
                missing.append(config_key)

        if missing:
            return HealthStatus(
                available=False,
                message=(
                    f"Missing required config fields: {', '.join(missing)}"
                ),
            )

        # 检查 SDK 可用性
        try:
            _import_sdk()
        except ImportError:
            return HealthStatus(
                available=False,
                message=(
                    "claude-agent-sdk not installed. "
                    "Install with: pip install hermes-agent[control-plane]"
                ),
            )

        model = self._config.get("model", "unknown")
        base_url = self._config.get("base_url", "official Anthropic API")

        return HealthStatus(
            available=True,
            message=f"Claude Agent SDK ready, model={model}, endpoint={base_url}",
        )

    # ── 资源清理 ──────────────────────────────────────────────

    def close(self) -> None:
        """清理资源。"""
        self._sessions.clear()
        self._abort_events.clear()
