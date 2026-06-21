"""Hermes V1.0.0 事件模型验收测试。

覆盖：
  - 每种事件类型都能构造且 JSON round-trip
  - Discriminated Union 能正确反序列化到具体子类
  - assistant.thinking 事件可被创建并有 thinking 字段
  - 工厂函数正常工作
  - ID 生成格式正确
  - 错误类继承层次正确
"""

from __future__ import annotations

import json

import pytest

from agent.control_plane.errors import (
    ApprovalDeniedError,
    ApprovalTimeoutError,
    HermesError,
    HermesRuntimeError,
    SessionNotFoundError,
    TurnNotFoundError,
    WorkspaceError,
    WorkspaceLockedError,
    WorkspaceNotFoundError,
)
from agent.control_plane.hermes_event import (
    HERMES_EVENT_TYPES,
    AssistantDeltaEvent,
    AssistantMessageEvent,
    AssistantThinkingEvent,
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    FileChangedEvent,
    HermesEventAdapter,
    QuestionRequestedEvent,
    SessionStartedEvent,
    ToolCompletedEvent,
    ToolOutputEvent,
    ToolStartedEvent,
    TurnCancelledEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
    deserialize_event,
    deserialize_event_dict,
    make_approval_requested,
    make_approval_resolved,
    make_assistant_delta,
    make_assistant_message,
    make_assistant_thinking,
    make_file_changed,
    make_question_requested,
    make_session_started,
    make_tool_completed,
    make_tool_output,
    make_tool_started,
    make_turn_cancelled,
    make_turn_completed,
    make_turn_failed,
    make_turn_started,
    serialize_event,
    serialize_event_dict,
)
from agent.control_plane.ids import (
    new_approval_id,
    new_event_id,
    new_session_id,
    new_turn_id,
)


# ─── 辅助 ──────────────────────────────────────────────────

SID = "sess_abc123"
TID = "turn_def456"


# ─── 事件构造 + JSON round-trip ─────────────────────────────

class TestEventConstructionAndRoundTrip:
    """每种事件都能构造、序列化、反序列化，且字段不丢失。"""

    @pytest.mark.parametrize(
        "make_fn, kwargs, check_type",
        [
            (
                make_session_started,
                dict(session_id=SID, provider="claude", seq=1),
                "session.started",
            ),
            (
                make_turn_started,
                dict(session_id=SID, turn_id=TID, seq=2),
                "turn.started",
            ),
            (
                make_turn_completed,
                dict(session_id=SID, turn_id=TID, summary="done", seq=3),
                "turn.completed",
            ),
            (
                make_turn_failed,
                dict(session_id=SID, turn_id=TID, error="oops", code="E001", seq=4),
                "turn.failed",
            ),
            (
                make_turn_cancelled,
                dict(session_id=SID, turn_id=TID, reason="user stop", seq=5),
                "turn.cancelled",
            ),
            (
                make_assistant_delta,
                dict(session_id=SID, turn_id=TID, text="hello", seq=6),
                "assistant.delta",
            ),
            (
                make_assistant_message,
                dict(session_id=SID, turn_id=TID, text="full msg", seq=7),
                "assistant.message",
            ),
            (
                make_assistant_thinking,
                dict(session_id=SID, turn_id=TID, text="hmm...", seq=8),
                "assistant.thinking",
            ),
            (
                make_tool_started,
                dict(
                    session_id=SID,
                    turn_id=TID,
                    tool_call_id="tc1",
                    tool_name="bash",
                    seq=9,
                ),
                "tool.started",
            ),
            (
                make_tool_output,
                dict(
                    session_id=SID,
                    turn_id=TID,
                    tool_call_id="tc1",
                    text="output",
                    seq=10,
                ),
                "tool.output",
            ),
            (
                make_tool_completed,
                dict(
                    session_id=SID,
                    turn_id=TID,
                    tool_call_id="tc1",
                    status="ok",
                    duration=230,
                    seq=11,
                ),
                "tool.completed",
            ),
            (
                make_file_changed,
                dict(
                    session_id=SID,
                    turn_id=TID,
                    path="src/app.py",
                    operation="edit",
                    diff="+new line",
                    seq=12,
                ),
                "file.changed",
            ),
            (
                make_approval_requested,
                dict(
                    session_id=SID,
                    turn_id=TID,
                    approval_id="apr1",
                    action={"category": "shell.command", "description": "Execute: ls"},
                    seq=13,
                ),
                "approval.requested",
            ),
            (
                make_approval_resolved,
                dict(
                    session_id=SID,
                    turn_id=TID,
                    approval_id="apr1",
                    decision="approved",
                    reason="ok",
                    seq=14,
                ),
                "approval.resolved",
            ),
            (
                make_question_requested,
                dict(
                    session_id=SID,
                    turn_id=TID,
                    question_id="q1",
                    question="What next?",
                    options=["A", "B"],
                    seq=15,
                ),
                "question.requested",
            ),
        ],
        ids=lambda x: x if isinstance(x, str) else "",
    )
    def test_roundtrip_json(self, make_fn, kwargs, check_type):
        """构造 → JSON 序列化 → 反序列化 → 字段一致。"""
        event = make_fn(**kwargs)
        assert event.type == check_type

        # JSON round-trip
        json_str = serialize_event(event)
        restored = deserialize_event(json_str)

        assert restored.type == check_type
        assert restored.session_id == SID
        assert restored.id == event.id
        assert restored.timestamp == event.timestamp

    @pytest.mark.parametrize(
        "make_fn, kwargs, check_type",
        [
            (
                make_session_started,
                dict(session_id=SID, provider="claude", seq=1),
                "session.started",
            ),
            (
                make_turn_started,
                dict(session_id=SID, turn_id=TID, seq=2),
                "turn.started",
            ),
            (
                make_turn_completed,
                dict(session_id=SID, turn_id=TID, summary="done", seq=3),
                "turn.completed",
            ),
            (
                make_assistant_thinking,
                dict(session_id=SID, turn_id=TID, text="thinking...", seq=8),
                "assistant.thinking",
            ),
        ],
    )
    def test_roundtrip_dict(self, make_fn, kwargs, check_type):
        """构造 → dict 序列化 → 反序列化 → 字段一致。"""
        event = make_fn(**kwargs)
        d = serialize_event_dict(event)
        assert d["type"] == check_type
        restored = deserialize_event_dict(d)
        assert restored.type == check_type
        assert restored.session_id == SID


# ─── Discriminated Union 反序列化 ───────────────────────────


class TestDiscriminatedUnion:
    """Union 能正确反序列化到具体子类。"""

    @pytest.mark.parametrize(
        "json_payload, expected_class",
        [
            (
                '{"type":"session.started","id":"e1","session_id":"s","timestamp":"t","seq":1}',
                SessionStartedEvent,
            ),
            (
                '{"type":"turn.started","id":"e2","session_id":"s","timestamp":"t","seq":2}',
                TurnStartedEvent,
            ),
            (
                '{"type":"turn.completed","id":"e3","session_id":"s","timestamp":"t","seq":3}',
                TurnCompletedEvent,
            ),
            (
                '{"type":"turn.failed","id":"e4","session_id":"s","timestamp":"t","seq":4,"error":"x"}',
                TurnFailedEvent,
            ),
            (
                '{"type":"turn.cancelled","id":"e5","session_id":"s","timestamp":"t","seq":5}',
                TurnCancelledEvent,
            ),
            (
                '{"type":"assistant.delta","id":"e6","session_id":"s","timestamp":"t","seq":6,"text":"hi"}',
                AssistantDeltaEvent,
            ),
            (
                '{"type":"assistant.message","id":"e7","session_id":"s","timestamp":"t","seq":7,"text":"msg"}',
                AssistantMessageEvent,
            ),
            (
                '{"type":"assistant.thinking","id":"e8","session_id":"s","timestamp":"t","seq":8,"text":"think"}',
                AssistantThinkingEvent,
            ),
            (
                '{"type":"tool.started","id":"e9","session_id":"s","timestamp":"t","seq":9,"tool_call_id":"tc","tool_name":"bash"}',
                ToolStartedEvent,
            ),
            (
                '{"type":"tool.output","id":"e10","session_id":"s","timestamp":"t","seq":10,"tool_call_id":"tc","text":"out"}',
                ToolOutputEvent,
            ),
            (
                '{"type":"tool.completed","id":"e11","session_id":"s","timestamp":"t","seq":11,"tool_call_id":"tc","status":"ok"}',
                ToolCompletedEvent,
            ),
            (
                '{"type":"file.changed","id":"e12","session_id":"s","timestamp":"t","seq":12,"path":"f","operation":"edit"}',
                FileChangedEvent,
            ),
            (
                '{"type":"approval.requested","id":"e13","session_id":"s","timestamp":"t","seq":13,"approval_id":"a"}',
                ApprovalRequestedEvent,
            ),
            (
                '{"type":"approval.resolved","id":"e14","session_id":"s","timestamp":"t","seq":14,"approval_id":"a","decision":"approved"}',
                ApprovalResolvedEvent,
            ),
            (
                '{"type":"question.requested","id":"e15","session_id":"s","timestamp":"t","seq":15,"question_id":"q","question":"what?"}',
                QuestionRequestedEvent,
            ),
        ],
    )
    def test_discriminated_union_type(self, json_payload, expected_class):
        event = deserialize_event(json_payload)
        assert isinstance(event, expected_class)


# ─── assistant.thinking 专项测试 ─────────────────────────────


class TestAssistantThinking:
    """assistant.thinking 事件的专项验证。"""

    def test_create_with_thinking_field(self):
        event = make_assistant_thinking(
            session_id=SID,
            turn_id=TID,
            text="Let me think about this...",
            provider_meta={"model": "deepseek-r1", "reasoning_tokens": 150},
            seq=8,
        )
        assert event.type == "assistant.thinking"
        assert event.text == "Let me think about this..."
        assert event.provider_meta is not None
        assert event.provider_meta["model"] == "deepseek-r1"
        assert event.provider_meta["reasoning_tokens"] == 150

    def test_json_roundtrip_with_provider_meta(self):
        event = make_assistant_thinking(
            session_id=SID,
            turn_id=TID,
            text="reasoning...",
            provider_meta={"model": "qwen-32b"},
        )
        json_str = serialize_event(event)
        restored = deserialize_event(json_str)
        assert isinstance(restored, AssistantThinkingEvent)
        assert restored.text == "reasoning..."
        assert restored.provider_meta == {"model": "qwen-32b"}

    def test_default_provider_meta_is_none(self):
        event = AssistantThinkingEvent(session_id=SID, text="no meta")
        assert event.provider_meta is None
        json_str = serialize_event(event)
        restored = deserialize_event(json_str)
        assert restored.provider_meta is None


# ─── 特定事件字段验证 ────────────────────────────────────────


class TestSpecificEventFields:
    """验证各种事件特有字段的序列化 / 反序列化。"""

    def test_turn_completed_summary(self):
        event = make_turn_completed(SID, TID, summary="All done!")
        json_str = serialize_event(event)
        restored = deserialize_event(json_str)
        assert isinstance(restored, TurnCompletedEvent)
        assert restored.summary == "All done!"
        assert restored.status == "success"

    def test_turn_failed_fields(self):
        event = make_turn_failed(
            SID, TID, error="API timeout", code="TIMEOUT", retryable=True,
        )
        json_str = serialize_event(event)
        restored = deserialize_event(json_str)
        assert isinstance(restored, TurnFailedEvent)
        assert restored.error == "API timeout"
        assert restored.code == "TIMEOUT"
        assert restored.retryable is True

    def test_tool_completed_with_duration(self):
        event = make_tool_completed(
            SID, TID, tool_call_id="tc_42", status="ok", duration=500,
        )
        json_str = serialize_event(event)
        restored = deserialize_event(json_str)
        assert isinstance(restored, ToolCompletedEvent)
        assert restored.tool_call_id == "tc_42"
        assert restored.duration == 500

    def test_file_changed_with_diff(self):
        event = make_file_changed(
            SID, TID, path="main.py", operation="create", diff="+print('hi')",
        )
        json_str = serialize_event(event)
        restored = deserialize_event(json_str)
        assert isinstance(restored, FileChangedEvent)
        assert restored.path == "main.py"
        assert restored.operation == "create"
        assert restored.diff == "+print('hi')"

    def test_approval_requested_with_action(self):
        action = {
            "category": "shell.command",
            "description": "Execute: rm -rf /tmp/test",
            "command": "rm -rf /tmp/test",
            "riskLevel": "high",
        }
        event = make_approval_requested(
            SID, TID, approval_id="apr_xyz", action=action,
        )
        json_str = serialize_event(event)
        restored = deserialize_event(json_str)
        assert isinstance(restored, ApprovalRequestedEvent)
        assert restored.approval_id == "apr_xyz"
        assert restored.action is not None
        assert restored.action["riskLevel"] == "high"

    def test_question_requested_with_options(self):
        event = make_question_requested(
            SID, TID,
            question_id="q1",
            question="Which approach?",
            options=["A: refactor", "B: rewrite"],
        )
        json_str = serialize_event(event)
        restored = deserialize_event(json_str)
        assert isinstance(restored, QuestionRequestedEvent)
        assert restored.question == "Which approach?"
        assert restored.options == ["A: refactor", "B: rewrite"]

    def test_session_started_provider(self):
        event = make_session_started(
            SID, provider="claude", provider_session_id="ps_123",
        )
        json_str = serialize_event(event)
        restored = deserialize_event(json_str)
        assert isinstance(restored, SessionStartedEvent)
        assert restored.provider == "claude"
        assert restored.provider_session_id == "ps_123"


# ─── HERMES_EVENT_TYPES 常量 ────────────────────────────────


class TestHermesEventTypes:
    """验证 16 种事件类型集合（V1.1 错误恢复 Wave B 加入 turn.retrying）。"""

    def test_count_is_16(self):
        assert len(HERMES_EVENT_TYPES) == 16

    def test_contains_all_types(self):
        expected = {
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
        assert HERMES_EVENT_TYPES == expected


# ─── ID 生成工具 ─────────────────────────────────────────────


class TestIdGeneration:
    """ID 格式验证。"""

    def test_session_id_prefix(self):
        sid = new_session_id()
        assert sid.startswith("sess_")
        assert len(sid) > 5

    def test_turn_id_prefix(self):
        tid = new_turn_id()
        assert tid.startswith("turn_")
        assert len(tid) > 5

    def test_event_id_prefix(self):
        eid = new_event_id()
        assert eid.startswith("evt_")
        assert len(eid) > 4

    def test_approval_id_prefix(self):
        aid = new_approval_id()
        assert aid.startswith("apr_")
        assert len(aid) > 4

    def test_ids_are_unique(self):
        ids = {new_session_id() for _ in range(100)}
        assert len(ids) == 100


# ─── 错误类 ─────────────────────────────────────────────────


class TestErrors:
    """错误类继承层次和属性验证。"""

    def test_hermes_error_base(self):
        err = HermesError("test error", code="E001")
        assert str(err) == "test error"
        assert err.code == "E001"

    def test_session_not_found(self):
        err = SessionNotFoundError("sess_xyz")
        assert "sess_xyz" in str(err)
        assert err.session_id == "sess_xyz"
        assert isinstance(err, HermesError)

    def test_turn_not_found(self):
        err = TurnNotFoundError("turn_xyz")
        assert "turn_xyz" in str(err)
        assert err.turn_id == "turn_xyz"
        assert isinstance(err, HermesError)

    def test_approval_denied(self):
        err = ApprovalDeniedError("apr_1", reason="too risky")
        assert "apr_1" in str(err)
        assert "too risky" in str(err)
        assert err.approval_id == "apr_1"
        assert err.reason == "too risky"
        assert isinstance(err, HermesError)

    def test_approval_timeout(self):
        err = ApprovalTimeoutError("apr_2", timeout_seconds=30.0)
        assert "30.0" in str(err)
        assert err.approval_id == "apr_2"
        assert isinstance(err, HermesError)

    def test_hermes_runtime_error(self):
        err = HermesRuntimeError("provider failed", code="RT001", provider="claude")
        assert str(err) == "provider failed"
        assert err.code == "RT001"
        assert err.provider == "claude"
        assert isinstance(err, HermesError)

    def test_workspace_locked(self):
        err = WorkspaceLockedError("ws_1", locked_by="sess_other")
        assert "ws_1" in str(err)
        assert "sess_other" in str(err)
        assert isinstance(err, WorkspaceError)
        assert isinstance(err, HermesError)

    def test_workspace_not_found(self):
        err = WorkspaceNotFoundError("ws_missing")
        assert "ws_missing" in str(err)
        assert isinstance(err, WorkspaceError)
        assert isinstance(err, HermesError)

    def test_error_hierarchy(self):
        """验证完整的继承链。"""
        assert issubclass(SessionNotFoundError, HermesError)
        assert issubclass(TurnNotFoundError, HermesError)
        assert issubclass(ApprovalDeniedError, HermesError)
        assert issubclass(ApprovalTimeoutError, HermesError)
        assert issubclass(HermesRuntimeError, HermesError)
        assert issubclass(WorkspaceError, HermesError)
        assert issubclass(WorkspaceLockedError, WorkspaceError)
        assert issubclass(WorkspaceNotFoundError, WorkspaceError)


# ─── JSON Schema 测试 ───────────────────────────────────────


class TestJsonSchema:
    """验证 TypeAdapter 能生成有效的 JSON Schema。"""

    def test_adapter_generates_schema(self):
        schema = HermesEventAdapter.json_schema()
        assert "oneOf" in schema or "anyOf" in schema
        # 应包含所有 15 种类型的定义
        schema_str = json.dumps(schema)
        for event_type in HERMES_EVENT_TYPES:
            assert event_type in schema_str, f"Missing event type in schema: {event_type}"


# ─── import 冒烟测试 ────────────────────────────────────────


def test_import_star():
    """验证 `from agent.control_plane.hermes_event import *` 不报错。"""
    import importlib
    mod = importlib.import_module("agent.control_plane.hermes_event")
    # 基本导出检查
    assert hasattr(mod, "SessionStartedEvent")
    assert hasattr(mod, "HermesEventAdapter")
    assert hasattr(mod, "serialize_event")
    assert hasattr(mod, "deserialize_event")
