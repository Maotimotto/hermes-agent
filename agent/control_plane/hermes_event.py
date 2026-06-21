"""Hermes V1.0.0 统一事件模型 — 15 种事件类型 + 工厂函数。

依据路书 02（核心接口定义）和 03（统一事件模型详解），采用 pydantic v2
实现 Discriminated Union，支持 JSON 序列化 / 反序列化。

事件类型（15 种）:
  session.started · turn.started · turn.completed · turn.failed ·
  turn.cancelled · assistant.delta · assistant.message · assistant.thinking ·
  tool.started · tool.output · tool.completed · file.changed ·
  approval.requested · approval.resolved · question.requested
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from agent.control_plane.ids import new_event_id

# ─── 基础事件模型 ───────────────────────────────────────────


class BaseEvent(BaseModel, extra="forbid"):
    """所有 HermesEvent 的公共字段。"""

    id: str = Field(default_factory=new_event_id)
    type: str
    session_id: str
    turn_id: str | None = None
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    seq: int = 0


# ─── Session 生命周期 ───────────────────────────────────────


class SessionStartedEvent(BaseEvent):
    """session 生命周期 — session 已创建并就绪。"""

    type: Literal["session.started"] = "session.started"
    provider: str = ""
    provider_session_id: str | None = None


# ─── Turn 生命周期 ──────────────────────────────────────────


class TurnStartedEvent(BaseEvent):
    """turn 生命周期 — turn 开始。"""

    type: Literal["turn.started"] = "turn.started"


class TurnCompletedEvent(BaseEvent):
    """turn 生命周期 — turn 正常完成。"""

    type: Literal["turn.completed"] = "turn.completed"
    status: Literal["success"] = "success"
    summary: str | None = None


class TurnFailedEvent(BaseEvent):
    """turn 生命周期 — turn 因错误失败。"""

    type: Literal["turn.failed"] = "turn.failed"
    error: str = ""
    code: str | None = None
    retryable: bool | None = None


class TurnRetryingEvent(BaseEvent):
    """turn 生命周期 — 自动重试中（V1.1 错误恢复 Wave B 新增）。

    在 turn 第一次失败后、第二次启动前发出，让前端能在 UI 上呈现
    「重试中…」状态而不是直接红字。

    字段：
      attempt: 即将发起的尝试序号（1 = 首次，2 = 第一次重试）
      reason: 来自 ClassifiedError.code（network/timeout/...）
      backoff_ms: 等待多少毫秒后重试
    """

    type: Literal["turn.retrying"] = "turn.retrying"
    attempt: int = 2
    reason: str = ""
    backoff_ms: int = 0


class TurnCancelledEvent(BaseEvent):
    """turn 生命周期 — 用户主动取消 turn。"""

    type: Literal["turn.cancelled"] = "turn.cancelled"
    reason: str | None = None


# ─── Assistant 输出 ─────────────────────────────────────────


class AssistantDeltaEvent(BaseEvent):
    """assistant 输出 — 流式文本片段。"""

    type: Literal["assistant.delta"] = "assistant.delta"
    text: str = ""


class AssistantMessageEvent(BaseEvent):
    """assistant 输出 — 完整消息文本。"""

    type: Literal["assistant.message"] = "assistant.message"
    text: str = ""


class AssistantThinkingEvent(BaseEvent):
    """assistant 输出 — thinking / reasoning 内容片段。🆕 V1.0.0 新增。

    来源：Claude Agent SDK 的 ThinkingBlock（ADR-001 实测发现），
    以及第三方中转商（如 OpenRouter）返回的 reasoning/thinking 内容。
    """

    type: Literal["assistant.thinking"] = "assistant.thinking"
    text: str = ""
    provider_meta: dict[str, Any] | None = None


# ─── Tool Call ──────────────────────────────────────────────


class ToolStartedEvent(BaseEvent):
    """tool call — 工具调用开始。"""

    type: Literal["tool.started"] = "tool.started"
    tool_call_id: str = ""
    tool_name: str = ""
    input: Any | None = None


class ToolOutputEvent(BaseEvent):
    """tool call — 工具执行的流式输出片段。"""

    type: Literal["tool.output"] = "tool.output"
    tool_call_id: str = ""
    text: str = ""


class ToolCompletedEvent(BaseEvent):
    """tool call — 工具执行完成。"""

    type: Literal["tool.completed"] = "tool.completed"
    tool_call_id: str = ""
    status: Literal["ok", "error"] = "ok"
    error: str | None = None
    duration: int | None = None  # ms


# ─── File Change ────────────────────────────────────────────


class FileChangedEvent(BaseEvent):
    """文件变更 — agent 创建、编辑或删除了文件。"""

    type: Literal["file.changed"] = "file.changed"
    path: str = ""
    operation: Literal["create", "edit", "delete"] = "edit"
    diff: str | None = None
    prev_hash: str | None = None
    new_hash: str | None = None


# ─── Approval ───────────────────────────────────────────────


class ApprovalRequestedEvent(BaseEvent):
    """审批 — agent 尝试执行需要用户审批的动作。"""

    type: Literal["approval.requested"] = "approval.requested"
    approval_id: str = ""
    action: dict[str, Any] | None = None


class ApprovalResolvedEvent(BaseEvent):
    """审批 — 用户对审批请求做出决策后。"""

    type: Literal["approval.resolved"] = "approval.resolved"
    approval_id: str = ""
    decision: Literal["approved", "denied"] = "approved"
    reason: str | None = None


# ─── User Question ──────────────────────────────────────────


class QuestionRequestedEvent(BaseEvent):
    """用户问题 — agent 向用户提出问题。"""

    type: Literal["question.requested"] = "question.requested"
    question_id: str = ""
    question: str = ""
    options: list[str] | None = None


# ─── Discriminated Union ───────────────────────────────────

HermesEvent = Annotated[
    Union[
        SessionStartedEvent,
        TurnStartedEvent,
        TurnCompletedEvent,
        TurnFailedEvent,
        TurnRetryingEvent,
        TurnCancelledEvent,
        AssistantDeltaEvent,
        AssistantMessageEvent,
        AssistantThinkingEvent,
        ToolStartedEvent,
        ToolOutputEvent,
        ToolCompletedEvent,
        FileChangedEvent,
        ApprovalRequestedEvent,
        ApprovalResolvedEvent,
        QuestionRequestedEvent,
    ],
    Field(discriminator="type"),
]

HERMES_EVENT_TYPES: set[str] = {
    "session.started",
    "turn.started",
    "turn.completed",
    "turn.failed",
    "turn.retrying",
    "turn.cancelled",
    "assistant.delta",
    "assistant.message",
    "assistant.thinking",
    "tool.started",
    "tool.output",
    "tool.completed",
    "file.changed",
    "approval.requested",
    "approval.resolved",
    "question.requested",
}

# ─── Type Adapter（用于 JSON 序列化 / 反序列化）──────────────

from pydantic import TypeAdapter  # noqa: E402

HermesEventAdapter: TypeAdapter[HermesEvent] = TypeAdapter(HermesEvent)


def serialize_event(event: HermesEvent) -> str:
    """将 HermesEvent 序列化为 JSON 字符串。"""
    return HermesEventAdapter.dump_json(event).decode("utf-8")


def deserialize_event(data: str | bytes) -> HermesEvent:
    """从 JSON 字符串反序列化为具体 HermesEvent 子类。"""
    return HermesEventAdapter.validate_json(data)


def serialize_event_dict(event: HermesEvent) -> dict[str, Any]:
    """将 HermesEvent 序列化为 dict（适合 JSON 序列化）。"""
    return HermesEventAdapter.dump_python(event, mode="json", by_alias=False)  # type: ignore[return-value]


def deserialize_event_dict(data: dict[str, Any]) -> HermesEvent:
    """从 dict 反序列化为具体 HermesEvent 子类。"""
    return HermesEventAdapter.validate_python(data)


# ─── 工厂函数 ──────────────────────────────────────────────


def make_session_started(
    session_id: str,
    provider: str = "",
    provider_session_id: str | None = None,
    turn_id: str | None = None,
    seq: int = 1,
) -> SessionStartedEvent:
    return SessionStartedEvent(
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        provider=provider,
        provider_session_id=provider_session_id,
    )


def make_turn_started(
    session_id: str,
    turn_id: str,
    seq: int = 0,
) -> TurnStartedEvent:
    return TurnStartedEvent(session_id=session_id, turn_id=turn_id, seq=seq)


def make_turn_completed(
    session_id: str,
    turn_id: str,
    summary: str | None = None,
    seq: int = 0,
) -> TurnCompletedEvent:
    return TurnCompletedEvent(
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        summary=summary,
    )


def make_turn_failed(
    session_id: str,
    turn_id: str,
    error: str = "",
    code: str | None = None,
    retryable: bool | None = None,
    seq: int = 0,
) -> TurnFailedEvent:
    return TurnFailedEvent(
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        error=error,
        code=code,
        retryable=retryable,
    )


def make_turn_cancelled(
    session_id: str,
    turn_id: str,
    reason: str | None = None,
    seq: int = 0,
) -> TurnCancelledEvent:
    return TurnCancelledEvent(
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        reason=reason,
    )


def make_turn_retrying(
    session_id: str,
    turn_id: str,
    *,
    attempt: int = 2,
    reason: str = "",
    backoff_ms: int = 0,
    seq: int = 0,
) -> TurnRetryingEvent:
    return TurnRetryingEvent(
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        attempt=attempt,
        reason=reason,
        backoff_ms=backoff_ms,
    )


def make_assistant_delta(
    session_id: str,
    turn_id: str,
    text: str = "",
    seq: int = 0,
) -> AssistantDeltaEvent:
    return AssistantDeltaEvent(
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        text=text,
    )


def make_assistant_message(
    session_id: str,
    turn_id: str,
    text: str = "",
    seq: int = 0,
) -> AssistantMessageEvent:
    return AssistantMessageEvent(
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        text=text,
    )


def make_assistant_thinking(
    session_id: str,
    turn_id: str,
    text: str = "",
    provider_meta: dict[str, Any] | None = None,
    seq: int = 0,
) -> AssistantThinkingEvent:
    return AssistantThinkingEvent(
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        text=text,
        provider_meta=provider_meta,
    )


def make_tool_started(
    session_id: str,
    turn_id: str,
    tool_call_id: str = "",
    tool_name: str = "",
    input: Any | None = None,
    seq: int = 0,
) -> ToolStartedEvent:
    return ToolStartedEvent(
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        input=input,
    )


def make_tool_output(
    session_id: str,
    turn_id: str,
    tool_call_id: str = "",
    text: str = "",
    seq: int = 0,
) -> ToolOutputEvent:
    return ToolOutputEvent(
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        tool_call_id=tool_call_id,
        text=text,
    )


def make_tool_completed(
    session_id: str,
    turn_id: str,
    tool_call_id: str = "",
    status: Literal["ok", "error"] = "ok",
    error: str | None = None,
    duration: int | None = None,
    seq: int = 0,
) -> ToolCompletedEvent:
    return ToolCompletedEvent(
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        tool_call_id=tool_call_id,
        status=status,
        error=error,
        duration=duration,
    )


def make_file_changed(
    session_id: str,
    turn_id: str,
    path: str = "",
    operation: Literal["create", "edit", "delete"] = "edit",
    diff: str | None = None,
    prev_hash: str | None = None,
    new_hash: str | None = None,
    seq: int = 0,
) -> FileChangedEvent:
    return FileChangedEvent(
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        path=path,
        operation=operation,
        diff=diff,
        prev_hash=prev_hash,
        new_hash=new_hash,
    )


def make_approval_requested(
    session_id: str,
    turn_id: str,
    approval_id: str = "",
    action: dict[str, Any] | None = None,
    seq: int = 0,
) -> ApprovalRequestedEvent:
    return ApprovalRequestedEvent(
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        approval_id=approval_id,
        action=action,
    )


def make_approval_resolved(
    session_id: str,
    turn_id: str,
    approval_id: str = "",
    decision: Literal["approved", "denied"] = "approved",
    reason: str | None = None,
    seq: int = 0,
) -> ApprovalResolvedEvent:
    return ApprovalResolvedEvent(
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        approval_id=approval_id,
        decision=decision,
        reason=reason,
    )


def make_question_requested(
    session_id: str,
    turn_id: str,
    question_id: str = "",
    question: str = "",
    options: list[str] | None = None,
    seq: int = 0,
) -> QuestionRequestedEvent:
    return QuestionRequestedEvent(
        session_id=session_id,
        turn_id=turn_id,
        seq=seq,
        question_id=question_id,
        question=question,
        options=options,
    )
