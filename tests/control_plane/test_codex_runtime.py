"""tests/control_plane/test_codex_runtime.py — CodexAppServerRuntime 测试。

最小验收：
  - Runtime 能被实例化，接口签名符合 ABC
  - mock CodexAppServerSession，验证 start_session 调用了现有三件套
  - 验证事件映射能产出正确的 HermesEvent 类型
  - 不连接真实 Codex 子进程
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent.control_plane.hermes_event import (
    AssistantDeltaEvent,
    AssistantMessageEvent,
    AssistantThinkingEvent,
    FileChangedEvent,
    ToolCompletedEvent,
    ToolOutputEvent,
    ToolStartedEvent,
    TurnCancelledEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)
from agent.control_plane.runtimes.codex_app_server_runtime import (
    CodexAppServerRuntime,
    _map_codex_notification_to_hermes_event,
)
from agent.control_plane.runtimes.interface import (
    AgentRuntime,
    HealthStatus,
    RuntimeConfig,
    RuntimeKind,
    SessionRef,
    StartSessionInput,
    TurnInput,
)
from agent.control_plane.store import SessionStore


# ─── 辅助 fixtures ────────────────────────────────────────────


@pytest.fixture
def mock_store() -> MagicMock:
    """Mock SessionStore，不连接真实 SQLite。"""
    store = MagicMock(spec=SessionStore)
    return store


@pytest.fixture
def runtime(mock_store: MagicMock) -> CodexAppServerRuntime:
    """创建 CodexAppServerRuntime 实例（不启动子进程）。"""
    return CodexAppServerRuntime(
        store=mock_store,
        codex_bin="codex",
    )


# ─── 接口合规性测试 ──────────────────────────────────────────


class TestInterfaceCompliance:
    """验证 CodexAppServerRuntime 实现了 AgentRuntime ABC 的所有方法。"""

    def test_is_subclass_of_agent_runtime(self, runtime: CodexAppServerRuntime):
        """CodexAppServerRuntime 必须是 AgentRuntime 的子类。"""
        assert isinstance(runtime, AgentRuntime)

    def test_has_provider_property(self, runtime: CodexAppServerRuntime):
        """provider 属性必须返回 'codex'。"""
        assert runtime.provider == "codex"

    def test_has_start_session(self, runtime: CodexAppServerRuntime):
        """必须实现 start_session 方法。"""
        assert hasattr(runtime, "start_session")
        assert callable(runtime.start_session)

    def test_has_start_turn(self, runtime: CodexAppServerRuntime):
        """必须实现 start_turn 方法。"""
        assert hasattr(runtime, "start_turn")
        assert callable(runtime.start_turn)

    def test_has_resume_session(self, runtime: CodexAppServerRuntime):
        """必须实现 resume_session 方法。"""
        assert hasattr(runtime, "resume_session")
        assert callable(runtime.resume_session)

    def test_has_steer_turn(self, runtime: CodexAppServerRuntime):
        """必须实现 steer_turn 方法。"""
        assert hasattr(runtime, "steer_turn")
        assert callable(runtime.steer_turn)

    def test_has_interrupt_turn(self, runtime: CodexAppServerRuntime):
        """必须实现 interrupt_turn 方法。"""
        assert hasattr(runtime, "interrupt_turn")
        assert callable(runtime.interrupt_turn)

    def test_has_resolve_approval(self, runtime: CodexAppServerRuntime):
        """必须实现 resolve_approval 方法。"""
        assert hasattr(runtime, "resolve_approval")
        assert callable(runtime.resolve_approval)

    def test_has_health_check(self, runtime: CodexAppServerRuntime):
        """必须实现 health_check 方法。"""
        assert hasattr(runtime, "health_check")
        assert callable(runtime.health_check)

    def test_can_instantiate_without_error(self, mock_store: MagicMock):
        """实例化不应报错。"""
        rt = CodexAppServerRuntime(store=mock_store)
        assert rt is not None


class TestRuntimeKind:
    """RuntimeKind 枚举测试。"""

    def test_codex_value(self):
        assert RuntimeKind.CODEX == "codex"

    def test_claude_value(self):
        assert RuntimeKind.CLAUDE == "claude"


class TestRuntimeConfig:
    """RuntimeConfig 数据类测试。"""

    def test_default_values(self):
        cfg = RuntimeConfig()
        assert cfg.kind == RuntimeKind.CODEX
        assert cfg.codex_bin == "codex"
        assert cfg.codex_home is None
        assert cfg.permission_profile is None

    def test_custom_values(self):
        cfg = RuntimeConfig(
            kind=RuntimeKind.CLAUDE,
            codex_bin="/usr/local/bin/codex",
            codex_home="/home/test/.codex",
            permission_profile="full-access",
        )
        assert cfg.kind == RuntimeKind.CLAUDE
        assert cfg.codex_bin == "/usr/local/bin/codex"


# ─── start_session 委托测试 ──────────────────────────────────


class TestStartSession:
    """验证 start_session 委托给现有三件套。"""

    @pytest.mark.asyncio
    async def test_delegates_to_session_ensure_started(
        self, mock_store: MagicMock
    ):
        """start_session 应委托 CodexAppServerSession.ensure_started()。

        【委托行】codex_app_server_runtime.py L184: session.ensure_started()
        """
        with patch(
            "agent.control_plane.runtimes.codex_app_server_runtime"
            ".CodexAppServerSession"
        ) as MockSession:
            mock_session_instance = MagicMock()
            mock_session_instance.ensure_started.return_value = "thread_abc123"
            MockSession.return_value = mock_session_instance

            rt = CodexAppServerRuntime(store=mock_store)
            ref = await rt.start_session(
                StartSessionInput(repo_path="/tmp/test-repo", branch="main")
            )

            # 验证委托：ensure_started 被调用
            mock_session_instance.ensure_started.assert_called_once()

            # 验证返回的 SessionRef
            assert isinstance(ref, SessionRef)
            assert ref.provider_session_id == "thread_abc123"
            assert ref.provider == "codex"

    @pytest.mark.asyncio
    async def test_session_cached_after_start(self, mock_store: MagicMock):
        """start_session 后 session 应被缓存。"""
        with patch(
            "agent.control_plane.runtimes.codex_app_server_runtime"
            ".CodexAppServerSession"
        ) as MockSession:
            mock_session_instance = MagicMock()
            mock_session_instance.ensure_started.return_value = "thread_xyz"
            MockSession.return_value = mock_session_instance

            rt = CodexAppServerRuntime(store=mock_store)
            ref = await rt.start_session(
                StartSessionInput(repo_path="/tmp/repo", branch="main")
            )

            assert rt._get_session("thread_xyz") is mock_session_instance

    @pytest.mark.asyncio
    async def test_creates_session_with_correct_params(
        self, mock_store: MagicMock
    ):
        """CodexAppServerSession 应以正确的参数创建。"""
        with patch(
            "agent.control_plane.runtimes.codex_app_server_runtime"
            ".CodexAppServerSession"
        ) as MockSession:
            mock_session_instance = MagicMock()
            mock_session_instance.ensure_started.return_value = "tid"
            MockSession.return_value = mock_session_instance

            rt = CodexAppServerRuntime(
                store=mock_store,
                codex_bin="/usr/bin/codex",
                codex_home="/home/test/.codex",
                permission_profile="full-access",
            )
            await rt.start_session(
                StartSessionInput(repo_path="/work/project", branch="dev")
            )

            # 验证 CodexAppServerSession 以正确参数创建
            MockSession.assert_called_once_with(
                cwd="/work/project",
                codex_bin="/usr/bin/codex",
                codex_home="/home/test/.codex",
                permission_profile="full-access",
                approval_callback=None,
            )


# ─── start_turn 委托测试 ─────────────────────────────────────


class TestStartTurn:
    """验证 start_turn 委托给现有三件套并产出 HermesEvent。"""

    @pytest.mark.asyncio
    async def test_yields_turn_started_event(self, mock_store: MagicMock):
        """start_turn 应首先 yield TurnStartedEvent。"""
        with patch(
            "agent.control_plane.runtimes.codex_app_server_runtime"
            ".CodexAppServerSession"
        ) as MockSession:
            mock_session_instance = MagicMock()
            mock_session_instance.ensure_started.return_value = "tid"

            # 模拟 run_turn 返回
            from agent.transports.codex_app_server_session import TurnResult

            mock_result = TurnResult(
                turn_id="turn_001",
                thread_id="tid",
                final_text="Hello!",
            )
            mock_session_instance.run_turn.return_value = mock_result
            MockSession.return_value = mock_session_instance

            rt = CodexAppServerRuntime(store=mock_store)
            await rt.start_session(
                StartSessionInput(repo_path="/tmp", branch="main")
            )

            events = []
            async for event in rt.start_turn("tid", TurnInput(prompt="hi")):
                events.append(event)

            # 第一个事件必须是 turn.started
            assert len(events) >= 1
            assert isinstance(events[0], TurnStartedEvent)
            assert events[0].session_id == "tid"

    @pytest.mark.asyncio
    async def test_yields_terminal_event_after_turn(self, mock_store: MagicMock):
        """run_turn 完成后应 yield 终端事件。"""
        with patch(
            "agent.control_plane.runtimes.codex_app_server_runtime"
            ".CodexAppServerSession"
        ) as MockSession:
            mock_session_instance = MagicMock()
            mock_session_instance.ensure_started.return_value = "tid"

            from agent.transports.codex_app_server_session import TurnResult

            mock_result = TurnResult(
                turn_id="turn_002",
                thread_id="tid",
                final_text="Done!",
            )
            mock_session_instance.run_turn.return_value = mock_result
            MockSession.return_value = mock_session_instance

            rt = CodexAppServerRuntime(store=mock_store)
            await rt.start_session(
                StartSessionInput(repo_path="/tmp", branch="main")
            )

            events = []
            async for event in rt.start_turn("tid", TurnInput(prompt="go")):
                events.append(event)

            # 最后一个事件必须是终端事件
            terminal = events[-1]
            assert isinstance(
                terminal, (TurnCompletedEvent, TurnFailedEvent, TurnCancelledEvent)
            )

    @pytest.mark.asyncio
    async def test_emits_turn_failed_on_error(self, mock_store: MagicMock):
        """run_turn 有 error 时应 yield TurnFailedEvent。"""
        with patch(
            "agent.control_plane.runtimes.codex_app_server_runtime"
            ".CodexAppServerSession"
        ) as MockSession:
            mock_session_instance = MagicMock()
            mock_session_instance.ensure_started.return_value = "tid"

            from agent.transports.codex_app_server_session import TurnResult

            mock_result = TurnResult(
                turn_id="turn_err",
                thread_id="tid",
                error="something went wrong",
            )
            mock_session_instance.run_turn.return_value = mock_result
            MockSession.return_value = mock_session_instance

            rt = CodexAppServerRuntime(store=mock_store)
            await rt.start_session(
                StartSessionInput(repo_path="/tmp", branch="main")
            )

            events = []
            async for event in rt.start_turn("tid", TurnInput(prompt="fail")):
                events.append(event)

            terminal = events[-1]
            assert isinstance(terminal, TurnFailedEvent)
            assert "something went wrong" in terminal.error

    @pytest.mark.asyncio
    async def test_raises_on_unknown_session(self, runtime: CodexAppServerRuntime):
        """对未知 session_id 调用 start_turn 应抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="no session"):
            async for _ in runtime.start_turn(
                "nonexistent", TurnInput(prompt="hi")
            ):
                pass

    @pytest.mark.asyncio
    async def test_on_event_maps_notifications(self, mock_store: MagicMock):
        """on_event 回调应将 Codex 通知映射为 HermesEvent。"""
        with patch(
            "agent.control_plane.runtimes.codex_app_server_runtime"
            ".CodexAppServerSession"
        ) as MockSession:
            mock_session_instance = MagicMock()
            mock_session_instance.ensure_started.return_value = "tid"

            from agent.transports.codex_app_server_session import TurnResult

            captured_callbacks = []

            def fake_run_turn(prompt, **kwargs):
                """模拟 run_turn：调用 on_event 发几个通知。"""
                cb = mock_session_instance._on_event
                if cb:
                    # 模拟 agentMessage 输出
                    cb({
                        "method": "item/completed",
                        "params": {
                            "item": {
                                "type": "agentMessage",
                                "id": "item_1",
                                "text": "Hello world",
                            }
                        },
                    })
                    # 模拟 tool 完成
                    cb({
                        "method": "item/completed",
                        "params": {
                            "item": {
                                "type": "commandExecution",
                                "id": "item_2",
                                "command": "ls -la",
                                "cwd": "/tmp",
                                "exitCode": 0,
                                "aggregatedOutput": "file1\nfile2",
                            }
                        },
                    })
                return TurnResult(
                    turn_id="turn_003",
                    thread_id="tid",
                    final_text="Hello world",
                )

            mock_session_instance.run_turn.side_effect = fake_run_turn
            MockSession.return_value = mock_session_instance

            rt = CodexAppServerRuntime(store=mock_store)
            await rt.start_session(
                StartSessionInput(repo_path="/tmp", branch="main")
            )

            events = []
            async for event in rt.start_turn("tid", TurnInput(prompt="do")):
                events.append(event)

            # 验证映射出的事件类型
            event_types = [type(e).__name__ for e in events]
            assert "TurnStartedEvent" in event_types
            assert "AssistantMessageEvent" in event_types
            assert "ToolCompletedEvent" in event_types

            # 验证具体内容
            assistant_events = [
                e for e in events if isinstance(e, AssistantMessageEvent)
            ]
            assert len(assistant_events) == 1
            assert assistant_events[0].text == "Hello world"


# ─── 事件映射补充层测试 ──────────────────────────────────────


class TestEventMapping:
    """测试 _map_codex_notification_to_hermes_event 补充映射函数。"""

    def test_agent_message_maps_to_assistant_message(self):
        """item/completed agentMessage → AssistantMessageEvent。"""
        notification = {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "agentMessage",
                    "id": "msg_1",
                    "text": "I can help with that.",
                }
            },
        }
        event = _map_codex_notification_to_hermes_event(
            notification, session_id="sess_1", turn_id="turn_1", seq=1
        )
        assert isinstance(event, AssistantMessageEvent)
        assert event.text == "I can help with that."
        assert event.session_id == "sess_1"
        assert event.turn_id == "turn_1"
        assert event.seq == 1

    def test_reasoning_maps_to_assistant_thinking(self):
        """★ 补充映射：item/completed reasoning → AssistantThinkingEvent。"""
        notification = {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "reasoning",
                    "id": "reason_1",
                    "summary": ["step 1", "step 2"],
                    "content": ["conclusion"],
                }
            },
        }
        event = _map_codex_notification_to_hermes_event(
            notification, session_id="s", turn_id="t", seq=2
        )
        assert isinstance(event, AssistantThinkingEvent)
        assert "step 1" in event.text
        assert "conclusion" in event.text

    def test_file_change_maps_to_file_changed(self):
        """★ 补充映射：item/completed fileChange → FileChangedEvent。"""
        notification = {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "fileChange",
                    "id": "fc_1",
                    "changes": [
                        {
                            "path": "src/main.py",
                            "kind": {"type": "update"},
                        }
                    ],
                    "status": "applied",
                }
            },
        }
        event = _map_codex_notification_to_hermes_event(
            notification, session_id="s", turn_id="t", seq=3
        )
        assert isinstance(event, FileChangedEvent)
        assert event.path == "src/main.py"
        assert event.operation == "edit"

    def test_command_execution_maps_to_tool_completed(self):
        """item/completed commandExecution → ToolCompletedEvent。"""
        notification = {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "commandExecution",
                    "id": "cmd_1",
                    "command": "echo hello",
                    "cwd": "/tmp",
                    "exitCode": 0,
                    "aggregatedOutput": "hello\n",
                }
            },
        }
        event = _map_codex_notification_to_hermes_event(
            notification, session_id="s", turn_id="t", seq=4
        )
        assert isinstance(event, ToolCompletedEvent)
        assert event.status == "ok"
        assert event.tool_call_id == "cmd_1"

    def test_command_execution_error_maps_to_tool_completed_error(self):
        """命令失败 → ToolCompletedEvent(status='error')。"""
        notification = {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "commandExecution",
                    "id": "cmd_err",
                    "command": "false",
                    "cwd": "/tmp",
                    "exitCode": 1,
                    "aggregatedOutput": "",
                }
            },
        }
        event = _map_codex_notification_to_hermes_event(
            notification, session_id="s", turn_id="t", seq=5
        )
        assert isinstance(event, ToolCompletedEvent)
        assert event.status == "error"

    def test_item_started_command_maps_to_tool_started(self):
        """item/started commandExecution → ToolStartedEvent。"""
        notification = {
            "method": "item/started",
            "params": {
                "item": {
                    "type": "commandExecution",
                    "id": "cmd_start",
                }
            },
        }
        event = _map_codex_notification_to_hermes_event(
            notification, session_id="s", turn_id="t", seq=6
        )
        assert isinstance(event, ToolStartedEvent)
        assert event.tool_name == "exec_command"

    def test_item_started_file_change_maps_to_tool_started(self):
        """item/started fileChange → ToolStartedEvent。"""
        notification = {
            "method": "item/started",
            "params": {
                "item": {
                    "type": "fileChange",
                    "id": "fc_start",
                }
            },
        }
        event = _map_codex_notification_to_hermes_event(
            notification, session_id="s", turn_id="t", seq=7
        )
        assert isinstance(event, ToolStartedEvent)
        assert event.tool_name == "apply_patch"

    def test_mcp_tool_call_maps_to_tool_started(self):
        """item/started mcpToolCall → ToolStartedEvent(tool_name='mcp.server.tool')。"""
        notification = {
            "method": "item/started",
            "params": {
                "item": {
                    "type": "mcpToolCall",
                    "id": "mcp_1",
                    "server": "github",
                    "tool": "search",
                }
            },
        }
        event = _map_codex_notification_to_hermes_event(
            notification, session_id="s", turn_id="t", seq=8
        )
        assert isinstance(event, ToolStartedEvent)
        assert event.tool_name == "mcp.github.search"

    def test_turn_completed_maps_to_turn_completed(self):
        """turn/completed → TurnCompletedEvent。"""
        notification = {
            "method": "turn/completed",
            "params": {
                "turn": {"id": "turn_done", "status": "completed"}
            },
        }
        event = _map_codex_notification_to_hermes_event(
            notification, session_id="s", turn_id="turn_done", seq=9
        )
        assert isinstance(event, TurnCompletedEvent)

    def test_turn_failed_maps_to_turn_failed(self):
        """turn/completed with failed → TurnFailedEvent。"""
        notification = {
            "method": "turn/completed",
            "params": {
                "turn": {
                    "id": "turn_fail",
                    "status": "failed",
                    "error": {"message": "internal error"},
                }
            },
        }
        event = _map_codex_notification_to_hermes_event(
            notification, session_id="s", turn_id="turn_fail", seq=10
        )
        assert isinstance(event, TurnFailedEvent)
        assert "internal error" in event.error

    def test_turn_interrupted_maps_to_turn_cancelled(self):
        """turn/completed with interrupted → TurnCancelledEvent。"""
        notification = {
            "method": "turn/completed",
            "params": {
                "turn": {"id": "turn_int", "status": "interrupted"}
            },
        }
        event = _map_codex_notification_to_hermes_event(
            notification, session_id="s", turn_id="turn_int", seq=11
        )
        assert isinstance(event, TurnCancelledEvent)

    def test_output_delta_maps_to_assistant_delta(self):
        """item/agentMessage/outputDelta → AssistantDeltaEvent。"""
        notification = {
            "method": "item/agentMessage/outputDelta",
            "params": {
                "delta": {"text": "hello "},
            },
        }
        event = _map_codex_notification_to_hermes_event(
            notification, session_id="s", turn_id="t", seq=12
        )
        assert isinstance(event, AssistantDeltaEvent)
        assert event.text == "hello "

    def test_unknown_notification_returns_none(self):
        """未识别通知应返回 None。"""
        notification = {
            "method": "thread/someEvent",
            "params": {},
        }
        event = _map_codex_notification_to_hermes_event(
            notification, session_id="s", turn_id="t", seq=13
        )
        assert event is None

    def test_file_change_create_kind(self):
        """fileChange with add kind → FileChangedEvent(operation='create')。"""
        notification = {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "fileChange",
                    "id": "fc_add",
                    "changes": [
                        {"path": "new_file.py", "kind": {"type": "add"}}
                    ],
                    "status": "applied",
                }
            },
        }
        event = _map_codex_notification_to_hermes_event(
            notification, session_id="s", turn_id="t", seq=14
        )
        assert isinstance(event, FileChangedEvent)
        assert event.operation == "create"

    def test_file_change_delete_kind(self):
        """fileChange with delete kind → FileChangedEvent(operation='delete')。"""
        notification = {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "fileChange",
                    "id": "fc_del",
                    "changes": [
                        {"path": "old.py", "kind": {"type": "delete"}}
                    ],
                    "status": "applied",
                }
            },
        }
        event = _map_codex_notification_to_hermes_event(
            notification, session_id="s", turn_id="t", seq=15
        )
        assert isinstance(event, FileChangedEvent)
        assert event.operation == "delete"

    def test_file_change_empty_changes(self):
        """fileChange with 空 changes → FileChangedEvent(path='')。"""
        notification = {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "fileChange",
                    "id": "fc_empty",
                    "changes": [],
                    "status": "applied",
                }
            },
        }
        event = _map_codex_notification_to_hermes_event(
            notification, session_id="s", turn_id="t", seq=16
        )
        assert isinstance(event, FileChangedEvent)
        assert event.path == ""


# ─── interrupt_turn 测试 ─────────────────────────────────────


class TestInterruptTurn:
    """验证 interrupt_turn 委托给 session.request_interrupt()。"""

    @pytest.mark.asyncio
    async def test_delegates_to_request_interrupt(self, mock_store: MagicMock):
        """interrupt_turn 应委托 session.request_interrupt()。"""
        with patch(
            "agent.control_plane.runtimes.codex_app_server_runtime"
            ".CodexAppServerSession"
        ) as MockSession:
            mock_session_instance = MagicMock()
            mock_session_instance.ensure_started.return_value = "tid"
            MockSession.return_value = mock_session_instance

            rt = CodexAppServerRuntime(store=mock_store)
            await rt.start_session(
                StartSessionInput(repo_path="/tmp", branch="main")
            )
            await rt.interrupt_turn("turn_xyz")

            # 委托行：session.request_interrupt()
            mock_session_instance.request_interrupt.assert_called_once()


# ─── steer_turn 测试 ─────────────────────────────────────────


class TestSteerTurn:
    """验证 steer_turn 静默忽略（Codex 不支持）。"""

    @pytest.mark.asyncio
    async def test_steer_turn_is_noop(self, runtime: CodexAppServerRuntime):
        """steer_turn 不应抛异常。"""
        await runtime.steer_turn("turn_1", "some input")


# ─── health_check 测试 ───────────────────────────────────────


class TestHealthCheck:
    """验证 health_check 委托 check_codex_binary()。"""

    @pytest.mark.asyncio
    async def test_health_check_returns_health_status(
        self, runtime: CodexAppServerRuntime
    ):
        """health_check 应返回 HealthStatus。"""
        with patch(
            "agent.control_plane.runtimes.codex_app_server_runtime"
            ".check_codex_binary"
        ) as mock_check:
            mock_check.return_value = (True, "0.130.0")
            status = await runtime.health_check()

            assert isinstance(status, HealthStatus)
            assert status.available is True
            assert "0.130.0" in status.message
            mock_check.assert_called_once_with("codex")

    @pytest.mark.asyncio
    async def test_health_check_unavailable(
        self, runtime: CodexAppServerRuntime
    ):
        """codex 不可用时 health_check 应返回 available=False。"""
        with patch(
            "agent.control_plane.runtimes.codex_app_server_runtime"
            ".check_codex_binary"
        ) as mock_check:
            mock_check.return_value = (False, "codex not found")
            status = await runtime.health_check()

            assert status.available is False


# ─── resume_session 测试 ─────────────────────────────────────


class TestResumeSession:
    """验证 resume_session 返回正确的 SessionRef。"""

    @pytest.mark.asyncio
    async def test_resume_returns_ref(self, runtime: CodexAppServerRuntime):
        ref = await runtime.resume_session("existing_thread_id")
        assert isinstance(ref, SessionRef)
        assert ref.provider_session_id == "existing_thread_id"
        assert ref.provider == "codex"


# ─── resolve_approval 测试 ───────────────────────────────────


class TestResolveApproval:
    """验证 resolve_approval 不抛异常。"""

    @pytest.mark.asyncio
    async def test_resolve_approval_noop(self, runtime: CodexAppServerRuntime):
        from agent.control_plane.runtimes.interface import ApprovalDecision

        decision = ApprovalDecision(
            request_id="apr_123",
            decision="approved",
            reason="ok",
        )
        # 不应抛异常
        await runtime.resolve_approval(decision)
