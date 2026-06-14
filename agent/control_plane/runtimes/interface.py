"""AgentRuntime 抽象基类 + 辅助类型定义。

依据路书 02（核心接口定义）§1 Python 版 AgentRuntime。
所有 provider（Codex、Claude 等）必须实现此 ABC。

辅助类型：
  - RuntimeKind: provider 种类枚举
  - RuntimeConfig: provider 配置
  - StartSessionInput / TurnInput / SessionRef / ApprovalDecision / HealthStatus
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import AsyncIterator, Literal

from agent.control_plane.hermes_event import HermesEvent


# ─── RuntimeKind 枚举 ─────────────────────────────────────────


class RuntimeKind(str, Enum):
    """Provider 种类枚举。"""

    CODEX = "codex"
    CLAUDE = "claude"


# ─── RuntimeConfig 数据类 ─────────────────────────────────────


@dataclass
class RuntimeConfig:
    """Provider 配置。"""

    kind: RuntimeKind = RuntimeKind.CODEX
    codex_bin: str = "codex"
    codex_home: str | None = None
    permission_profile: str | None = None
    credential: str | None = None  # Claude: API key; Codex: 不需要
    options: dict[str, str] | None = None


# ─── 输入类型 ─────────────────────────────────────────────────


@dataclass
class StartSessionInput:
    """启动 session 的输入参数。"""

    repo_path: str           # 工作区根目录（git repo 路径）
    branch: str              # 工作区分支名
    config: RuntimeConfig | None = None
    # Hermes 侧已生成的 session ID。路由层（routes/sessions.py）在调
    # start_session 前已经 new_session_id() 并写库，把它传进来让 runtime
    # 用同一个 ID 作为内部 _sessions 字典的 key，避免 turn 路由查不到 session。
    # 旧的 codex runtime 用 provider thread_id 作 key 是 Wave 8.3 遗留 bug，
    # 修复后 codex/claude 都以 hermes_session_id 为统一索引键。
    hermes_session_id: str | None = None


@dataclass
class TurnInput:
    """一个 turn 的输入参数。"""

    prompt: str                                   # 用户 prompt 文本
    system_prompt: str | None = None              # 可选的系统提示
    context_files: dict[str, str] | None = None   # 附加文件上下文
    metadata: dict[str, object] | None = None     # provider-specific 元数据


# ─── 输出类型 ─────────────────────────────────────────────────


@dataclass
class SessionRef:
    """session 引用。"""

    hermes_session_id: str           # Hermes 内部 session ID
    provider_session_id: str         # provider 侧的 session/thread ID
    provider: str                    # provider 名称
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ApprovalDecision:
    """审批决策。"""

    request_id: str                           # 审批请求 ID
    decision: Literal["approved", "denied"]   # 决策
    reason: str | None = None                 # 用户备注
    remember_for: int | None = None           # 记住时长（秒），0 = 仅本次


@dataclass
class HealthStatus:
    """健康检查结果。"""

    available: bool
    message: str
    version: str | None = None    # provider 版本信息
    latency: int | None = None    # 延迟（ms）


# ─── 主接口 ───────────────────────────────────────────────────


class AgentRuntime(ABC):
    """Hermes 最核心的抽象。所有 provider 必须实现此 ABC。

    实现者：
    - CodexAppServerRuntime（委托 agent/transports/codex_app_server* 三件套）
    - ClaudeAgentSdkRuntime（未来实现）
    """

    @property
    @abstractmethod
    def provider(self) -> Literal["claude", "codex"]:
        """provider 标识。"""
        ...

    # ── 生命周期 ──────────────────────────────────────────────

    @abstractmethod
    async def start_session(self, input: StartSessionInput) -> SessionRef:
        """启动一个新 session。完成后 provider 侧应已准备好接受 turn。"""
        ...

    @abstractmethod
    async def start_turn(
        self,
        session_id: str,
        input: TurnInput,
        signal: object | None = None,  # 类似 AbortSignal
    ) -> AsyncIterator[HermesEvent]:
        """在 session 中启动一个 turn，返回 HermesEvent 异步迭代器。

        实现要点：
        - 必须是惰性的（调用时才启动 turn）
        - 必须能正确处理中断
        - provider 原始事件必须转换为 HermesEvent
        """
        ...

    @abstractmethod
    async def resume_session(self, provider_session_id: str) -> SessionRef:
        """恢复一个已存在的 provider session。"""
        ...

    @abstractmethod
    async def steer_turn(self, turn_id: str, input: str) -> None:
        """向运行中的 turn 注入额外指令。"""
        ...

    @abstractmethod
    async def interrupt_turn(self, turn_id: str) -> None:
        """中断运行中的 turn。"""
        ...

    # ── 审批 ──────────────────────────────────────────────────

    @abstractmethod
    async def resolve_approval(self, decision: ApprovalDecision) -> None:
        """将 Hermes 审批决策回传给 provider。由 Approval Gate 调用。"""
        ...

    # ── 健康检查 ──────────────────────────────────────────────

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """检查 provider 是否可用。"""
        ...
