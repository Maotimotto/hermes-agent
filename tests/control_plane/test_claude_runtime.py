"""tests/control_plane/test_claude_runtime.py — ClaudeAgentSdkRuntime 测试。

最小验收：
  - Runtime 能被实例化，接口签名符合 AgentRuntime ABC
  - mock SDK 消息流，验证事件转换（含 ThinkingBlock → assistant.thinking）
  - 验证 env 注入逻辑（mock claude-agent-sdk，检查传入的 env dict 包含三项）
  - 验证 token 不出现在日志里（抓日志字符串不包含 token）
  - 不起真子进程
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.control_plane.hermes_event import (
    AssistantDeltaEvent,
    AssistantThinkingEvent,
    SessionStartedEvent,
    ToolCompletedEvent,
    ToolStartedEvent,
    TurnCancelledEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)
from agent.control_plane.runtimes.claude_agent_sdk_runtime import (
    ClaudeAgentSdkRuntime,
    _map_sdk_message_to_hermes_event,
    _mask_token,
    build_env_from_config,
)
from agent.control_plane.runtimes.interface import (
    AgentRuntime,
    ApprovalDecision,
    HealthStatus,
    SessionRef,
    StartSessionInput,
    TurnInput,
)


# ─── Mock SDK 消息类型 ────────────────────────────────────────
#
# 不导入真正的 claude-agent-sdk（76MB 可选依赖），
# 用轻量 mock 类模拟 SDK 消息结构。


class ThinkingBlock:
    """模拟 SDK ThinkingBlock。"""

    def __init__(self, thinking: str) -> None:
        self.thinking = thinking


class TextBlock:
    """模拟 SDK TextBlock。"""

    def __init__(self, text: str) -> None:
        self.text = text


class ToolUseBlock:
    """模拟 SDK ToolUseBlock。"""

    def __init__(self, id: str, name: str, input: Any = None) -> None:
        self.id = id
        self.name = name
        self.input = input


class ToolResultBlock:
    """模拟 SDK ToolResultBlock。"""

    def __init__(
        self,
        tool_use_id: str,
        is_error: bool = False,
    ) -> None:
        self.tool_use_id = tool_use_id
        self.is_error = is_error


class SystemMessage:
    """模拟 SDK SystemMessage。"""

    def __init__(self, subtype: str = "", session_id: str = "") -> None:
        self.subtype = subtype
        self.session_id = session_id


class AssistantMessage:
    """模拟 SDK AssistantMessage。"""

    def __init__(self, content: list[Any]) -> None:
        self.content = content


class UserMessage:
    """模拟 SDK UserMessage。"""

    def __init__(self, content: list[Any]) -> None:
        self.content = content


class ResultMessage:
    """模拟 SDK ResultMessage。"""

    def __init__(self, result: str = "") -> None:
        self.result = result


# ─── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def claude_config() -> dict[str, str]:
    """标准 Claude 配置（cpass.cc 中转商格式）。"""
    return {
        "base_url": "https://www.cpass.cc",
        "api_key": "sk-UbS1234567890abcdef",
        "model": "claude-opus-4-6",
    }


@pytest.fixture
def runtime(claude_config: dict[str, str]) -> ClaudeAgentSdkRuntime:
    """创建 ClaudeAgentSdkRuntime 实例。"""
    return ClaudeAgentSdkRuntime(config=claude_config)


@pytest.fixture
def mock_sdk_classes():
    """Mock claude_agent_sdk 的 ClaudeSDKClient 和 ClaudeAgentOptions。"""
    mock_client_cls = MagicMock()
    mock_options_cls = MagicMock()

    # mock_client 是 async context manager
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    # query() 需要可 await
    mock_client.query = AsyncMock()

    return mock_client_cls, mock_options_cls, mock_client


# ─── 接口合规性测试 ──────────────────────────────────────────


class TestInterfaceCompliance:
    """验证 ClaudeAgentSdkRuntime 实现了 AgentRuntime ABC 的所有方法。"""

    def test_is_subclass_of_agent_runtime(self, runtime: ClaudeAgentSdkRuntime):
        """ClaudeAgentSdkRuntime 必须是 AgentRuntime 的子类。"""
        assert isinstance(runtime, AgentRuntime)

    def test_has_provider_property(self, runtime: ClaudeAgentSdkRuntime):
        """provider 属性必须返回 'claude'。"""
        assert runtime.provider == "claude"

    def test_has_start_session(self, runtime: ClaudeAgentSdkRuntime):
        assert hasattr(runtime, "start_session")
        assert callable(runtime.start_session)

    def test_has_start_turn(self, runtime: ClaudeAgentSdkRuntime):
        assert hasattr(runtime, "start_turn")
        assert callable(runtime.start_turn)

    def test_has_resume_session(self, runtime: ClaudeAgentSdkRuntime):
        assert hasattr(runtime, "resume_session")
        assert callable(runtime.resume_session)

    def test_has_steer_turn(self, runtime: ClaudeAgentSdkRuntime):
        assert hasattr(runtime, "steer_turn")
        assert callable(runtime.steer_turn)

    def test_has_interrupt_turn(self, runtime: ClaudeAgentSdkRuntime):
        assert hasattr(runtime, "interrupt_turn")
        assert callable(runtime.interrupt_turn)

    def test_has_resolve_approval(self, runtime: ClaudeAgentSdkRuntime):
        assert hasattr(runtime, "resolve_approval")
        assert callable(runtime.resolve_approval)

    def test_has_health_check(self, runtime: ClaudeAgentSdkRuntime):
        assert hasattr(runtime, "health_check")
        assert callable(runtime.health_check)

    def test_can_instantiate(self, claude_config: dict[str, str]):
        """实例化不应报错。"""
        rt = ClaudeAgentSdkRuntime(config=claude_config)
        assert rt is not None


# ─── 环境变量构建测试 ────────────────────────────────────────


class TestBuildEnvFromConfig:
    """验证 build_env_from_config 从 config 正确构建 env dict。"""

    def test_full_config_produces_three_env_vars(self):
        """完整 config（含 base_url）→ 三个环境变量。"""
        config = {
            "base_url": "https://www.cpass.cc",
            "api_key": "sk-UbS1234567890",
            "model": "claude-opus-4-6",
        }
        env = build_env_from_config(config)

        assert env["ANTHROPIC_BASE_URL"] == "https://www.cpass.cc"
        assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-UbS1234567890"
        assert env["ANTHROPIC_MODEL"] == "claude-opus-4-6"

    def test_official_anthropic_no_base_url(self):
        """官方 Anthropic 场景（无 base_url）→ 两个环境变量。"""
        config = {
            "api_key": "sk-ant-api03-xxxxx",
            "model": "claude-sonnet-4-20250514",
        }
        env = build_env_from_config(config)

        assert "ANTHROPIC_BASE_URL" not in env
        assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-ant-api03-xxxxx"
        assert env["ANTHROPIC_MODEL"] == "claude-sonnet-4-20250514"

    def test_missing_api_key_raises(self):
        """缺少 api_key → ValueError。"""
        config = {"model": "claude-opus-4-6"}
        with pytest.raises(ValueError, match="api_key"):
            build_env_from_config(config)

    def test_missing_model_raises(self):
        """缺少 model → ValueError。"""
        config = {"api_key": "sk-xxx"}
        with pytest.raises(ValueError, match="model"):
            build_env_from_config(config)

    def test_empty_base_url_omitted(self):
        """空字符串 base_url → 不注入 ANTHROPIC_BASE_URL。"""
        config = {
            "base_url": "",
            "api_key": "sk-xxx",
            "model": "claude-opus-4-6",
        }
        env = build_env_from_config(config)
        assert "ANTHROPIC_BASE_URL" not in env

    def test_env_keys_are_uppercase_underscore(self):
        """环境变量名必须是大写下划线格式 ANTHROPIC_*。"""
        config = {
            "base_url": "https://example.com",
            "api_key": "sk-xxx",
            "model": "test",
        }
        env = build_env_from_config(config)
        for key in env:
            assert key == key.upper(), f"Env key {key} is not uppercase"
            assert "_" in key, f"Env key {key} has no underscore"


# ─── Token 脱敏测试 ──────────────────────────────────────────


class TestTokenMasking:
    """验证 _mask_token 脱敏逻辑。"""

    def test_normal_token(self):
        """正常 token → 前 6 字符 + ***。"""
        assert _mask_token("sk-UbS1234567890abcdef") == "sk-UbS***"

    def test_short_token(self):
        """短 token（<=6 字符）→ ***。"""
        assert _mask_token("sk-xx") == "***"
        assert _mask_token("abc") == "***"

    def test_exact_6_chars(self):
        """恰好 6 字符 → ***。"""
        assert _mask_token("abcdef") == "***"


# ─── SDK 消息映射测试 ────────────────────────────────────────


class TestEventMapping:
    """验证 _map_sdk_message_to_hermes_event 各种 SDK 消息类型的映射。"""

    def test_system_message_init_maps_to_session_started(self):
        """SystemMessage(init) → SessionStartedEvent。"""
        msg = SystemMessage(subtype="init", session_id="sdk_sess_001")
        events = _map_sdk_message_to_hermes_event(
            msg, session_id="sess_1", turn_id="turn_1", seq=1,
        )

        assert len(events) == 1
        assert isinstance(events[0], SessionStartedEvent)
        assert events[0].provider == "claude"
        assert events[0].provider_session_id == "sdk_sess_001"
        assert events[0].session_id == "sess_1"
        assert events[0].seq == 1

    def test_system_message_non_init_ignored(self):
        """SystemMessage(非 init) → 忽略。"""
        msg = SystemMessage(subtype="setup")
        events = _map_sdk_message_to_hermes_event(
            msg, session_id="s", turn_id="t", seq=2,
        )
        assert events == []

    def test_assistant_thinking_block(self):
        """★ AssistantMessage + ThinkingBlock → AssistantThinkingEvent。"""
        msg = AssistantMessage(
            content=[ThinkingBlock(thinking="Let me think step by step...")]
        )
        events = _map_sdk_message_to_hermes_event(
            msg, session_id="s", turn_id="t", seq=3,
        )

        assert len(events) == 1
        assert isinstance(events[0], AssistantThinkingEvent)
        assert events[0].text == "Let me think step by step..."

    def test_assistant_text_block(self):
        """AssistantMessage + TextBlock → AssistantDeltaEvent。"""
        msg = AssistantMessage(
            content=[TextBlock(text="Hello! How can I help?")]
        )
        events = _map_sdk_message_to_hermes_event(
            msg, session_id="s", turn_id="t", seq=4,
        )

        assert len(events) == 1
        assert isinstance(events[0], AssistantDeltaEvent)
        assert events[0].text == "Hello! How can I help?"

    def test_assistant_tool_use_block(self):
        """AssistantMessage + ToolUseBlock → ToolStartedEvent。"""
        msg = AssistantMessage(
            content=[
                ToolUseBlock(
                    id="tu_001",
                    name="exec_command",
                    input={"command": "ls -la"},
                )
            ]
        )
        events = _map_sdk_message_to_hermes_event(
            msg, session_id="s", turn_id="t", seq=5,
        )

        assert len(events) == 1
        assert isinstance(events[0], ToolStartedEvent)
        assert events[0].tool_call_id == "tu_001"
        assert events[0].tool_name == "exec_command"
        assert events[0].input == {"command": "ls -la"}

    def test_assistant_mixed_blocks(self):
        """AssistantMessage 含多个 block → 多个 HermesEvent。"""
        msg = AssistantMessage(
            content=[
                ThinkingBlock(thinking="reasoning..."),
                TextBlock(text="Here's the result"),
                ToolUseBlock(id="tu_002", name="read_file"),
            ]
        )
        events = _map_sdk_message_to_hermes_event(
            msg, session_id="s", turn_id="t", seq=6,
        )

        assert len(events) == 3
        assert isinstance(events[0], AssistantThinkingEvent)
        assert isinstance(events[1], AssistantDeltaEvent)
        assert isinstance(events[2], ToolStartedEvent)

    def test_user_message_tool_result(self):
        """UserMessage + ToolResultBlock(ok) → ToolCompletedEvent。"""
        msg = UserMessage(
            content=[ToolResultBlock(tool_use_id="tu_001", is_error=False)]
        )
        events = _map_sdk_message_to_hermes_event(
            msg, session_id="s", turn_id="t", seq=7,
        )

        assert len(events) == 1
        assert isinstance(events[0], ToolCompletedEvent)
        assert events[0].tool_call_id == "tu_001"
        assert events[0].status == "ok"

    def test_user_message_tool_result_error(self):
        """UserMessage + ToolResultBlock(error) → ToolCompletedEvent(status='error')。"""
        msg = UserMessage(
            content=[ToolResultBlock(tool_use_id="tu_002", is_error=True)]
        )
        events = _map_sdk_message_to_hermes_event(
            msg, session_id="s", turn_id="t", seq=8,
        )

        assert len(events) == 1
        assert isinstance(events[0], ToolCompletedEvent)
        assert events[0].status == "error"

    def test_result_message(self):
        """ResultMessage → TurnCompletedEvent。"""
        msg = ResultMessage(result="Task completed successfully")
        events = _map_sdk_message_to_hermes_event(
            msg, session_id="s", turn_id="t", seq=9,
        )

        assert len(events) == 1
        assert isinstance(events[0], TurnCompletedEvent)
        assert events[0].summary == "Task completed successfully"

    def test_unknown_message_type_ignored(self):
        """未知消息类型 → 忽略。"""
        msg = MagicMock(spec=[])  # 无 __name__ 匹配
        type(msg).__name__ = "UnknownMessageType"
        events = _map_sdk_message_to_hermes_event(
            msg, session_id="s", turn_id="t", seq=10,
        )
        assert events == []

    def test_full_sequence_simulation(self):
        """模拟完整事件序列：SystemMessage(init) → ThinkingBlock → Text → Result。"""
        messages = [
            SystemMessage(subtype="init", session_id="sdk_123"),
            AssistantMessage(
                content=[ThinkingBlock(thinking="Let me analyze...")]
            ),
            AssistantMessage(content=[TextBlock(text="Hello!")]),
            ResultMessage(result="done"),
        ]

        all_events = []
        for i, msg in enumerate(messages):
            all_events.extend(
                _map_sdk_message_to_hermes_event(
                    msg, session_id="s", turn_id="t", seq=i + 1,
                )
            )

        types = [type(e).__name__ for e in all_events]
        assert types == [
            "SessionStartedEvent",
            "AssistantThinkingEvent",
            "AssistantDeltaEvent",
            "TurnCompletedEvent",
        ]


# ─── start_session 测试 ──────────────────────────────────────


class TestStartSession:
    """验证 start_session 返回正确的 SessionRef。"""

    @pytest.mark.asyncio
    async def test_returns_session_ref(self, runtime: ClaudeAgentSdkRuntime):
        ref = await runtime.start_session(
            StartSessionInput(repo_path="/tmp/test", branch="main")
        )
        assert isinstance(ref, SessionRef)
        assert ref.provider == "claude"
        assert ref.provider_session_id == ""  # 首次 turn 后回填

    @pytest.mark.asyncio
    async def test_session_cached(self, runtime: ClaudeAgentSdkRuntime):
        ref = await runtime.start_session(
            StartSessionInput(repo_path="/tmp/test", branch="main")
        )
        assert ref.hermes_session_id in runtime._sessions


# ─── start_turn 测试 ─────────────────────────────────────────


class TestStartTurn:
    """验证 start_turn 调用 SDK 并正确映射事件。"""

    @pytest.mark.asyncio
    async def test_yields_turn_started_first(
        self,
        runtime: ClaudeAgentSdkRuntime,
        mock_sdk_classes: tuple,
        claude_config: dict[str, str],
    ):
        """start_turn 应首先 yield TurnStartedEvent。"""
        mock_client_cls, mock_options_cls, mock_client = mock_sdk_classes

        async def fake_aiter():
            yield ResultMessage(result="done")

        mock_client.receive_response.return_value = fake_aiter()

        with patch(
            "agent.control_plane.runtimes.claude_agent_sdk_runtime._import_sdk",
            return_value=(mock_client_cls, mock_options_cls),
        ):
            events = []
            async for event in runtime.start_turn(
                "sess_1", TurnInput(prompt="hi")
            ):
                events.append(event)

            assert len(events) >= 1
            assert isinstance(events[0], TurnStartedEvent)

    @pytest.mark.asyncio
    async def test_maps_sdk_messages_to_hermes_events(
        self,
        runtime: ClaudeAgentSdkRuntime,
        mock_sdk_classes: tuple,
    ):
        """SDK 消息流应正确映射为 HermesEvent。"""
        mock_client_cls, mock_options_cls, mock_client = mock_sdk_classes

        sdk_messages = [
            SystemMessage(subtype="init", session_id="sdk_abc"),
            AssistantMessage(
                content=[ThinkingBlock(thinking="thinking...")]
            ),
            AssistantMessage(content=[TextBlock(text="Hello!")]),
            ResultMessage(result="done"),
        ]

        async def fake_aiter():
            for m in sdk_messages:
                yield m

        mock_client.receive_response.return_value = fake_aiter()

        with patch(
            "agent.control_plane.runtimes.claude_agent_sdk_runtime._import_sdk",
            return_value=(mock_client_cls, mock_options_cls),
        ):
            events = []
            async for event in runtime.start_turn(
                "sess_1", TurnInput(prompt="test")
            ):
                events.append(event)

            event_types = [type(e).__name__ for e in events]
            assert "TurnStartedEvent" in event_types
            assert "SessionStartedEvent" in event_types
            assert "AssistantThinkingEvent" in event_types
            assert "AssistantDeltaEvent" in event_types

    @pytest.mark.asyncio
    async def test_env_injection_contains_three_keys(
        self,
        runtime: ClaudeAgentSdkRuntime,
        mock_sdk_classes: tuple,
        claude_config: dict[str, str],
    ):
        """★ 验证 env 注入：ClaudeAgentOptions 构造时传入的 env 包含三项。"""
        mock_client_cls, mock_options_cls, mock_client = mock_sdk_classes

        async def fake_aiter():
            yield ResultMessage()

        mock_client.receive_response.return_value = fake_aiter()

        with patch(
            "agent.control_plane.runtimes.claude_agent_sdk_runtime._import_sdk",
            return_value=(mock_client_cls, mock_options_cls),
        ):
            events = []
            async for event in runtime.start_turn(
                "sess_1", TurnInput(prompt="test")
            ):
                events.append(event)

            # 验证 ClaudeAgentOptions 被调用时传入的 env
            mock_options_cls.assert_called_once()
            call_kwargs = mock_options_cls.call_args
            env = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env")

            assert env is not None
            assert "ANTHROPIC_AUTH_TOKEN" in env
            assert "ANTHROPIC_MODEL" in env
            assert "ANTHROPIC_BASE_URL" in env
            assert env["ANTHROPIC_AUTH_TOKEN"] == claude_config["api_key"]
            assert env["ANTHROPIC_MODEL"] == claude_config["model"]
            assert env["ANTHROPIC_BASE_URL"] == claude_config["base_url"]

    @pytest.mark.asyncio
    async def test_token_not_in_logs(
        self,
        runtime: ClaudeAgentSdkRuntime,
        mock_sdk_classes: tuple,
        claude_config: dict[str, str],
        caplog: pytest.LogCaptureFixture,
    ):
        """★ 验证 token 不出现在日志中（安全要求）。"""
        mock_client_cls, mock_options_cls, mock_client = mock_sdk_classes

        async def fake_aiter():
            yield ResultMessage()

        mock_client.receive_response.return_value = fake_aiter()

        with patch(
            "agent.control_plane.runtimes.claude_agent_sdk_runtime._import_sdk",
            return_value=(mock_client_cls, mock_options_cls),
        ):
            with caplog.at_level(logging.DEBUG, logger="agent.control_plane.runtimes.claude_agent_sdk_runtime"):
                events = []
                async for event in runtime.start_turn(
                    "sess_1", TurnInput(prompt="test")
                ):
                    events.append(event)

            # token 完整值不应出现在任何日志中
            full_token = claude_config["api_key"]
            for record in caplog.records:
                assert full_token not in record.getMessage(), (
                    f"Token leaked in log: {record.getMessage()}"
                )

    @pytest.mark.asyncio
    async def test_yields_turn_completed_at_end(
        self,
        runtime: ClaudeAgentSdkRuntime,
        mock_sdk_classes: tuple,
    ):
        """SDK 迭代结束后应 yield 终端事件。"""
        mock_client_cls, mock_options_cls, mock_client = mock_sdk_classes

        sdk_messages = [
            AssistantMessage(content=[TextBlock(text="hi")]),
            ResultMessage(result="done"),
        ]

        async def fake_aiter():
            for m in sdk_messages:
                yield m

        mock_client.receive_response.return_value = fake_aiter()

        with patch(
            "agent.control_plane.runtimes.claude_agent_sdk_runtime._import_sdk",
            return_value=(mock_client_cls, mock_options_cls),
        ):
            events = []
            async for event in runtime.start_turn(
                "sess_1", TurnInput(prompt="go")
            ):
                events.append(event)

            terminal = events[-1]
            assert isinstance(terminal, TurnCompletedEvent)

    @pytest.mark.asyncio
    async def test_missing_config_yields_turn_failed(
        self,
        mock_sdk_classes: tuple,
    ):
        """config 缺失时 start_turn 应 yield TurnFailedEvent。"""
        # 构造缺少 api_key 的 config
        runtime = ClaudeAgentSdkRuntime(config={"model": "test"})
        mock_client_cls, mock_options_cls, mock_client = mock_sdk_classes

        with patch(
            "agent.control_plane.runtimes.claude_agent_sdk_runtime._import_sdk",
            return_value=(mock_client_cls, mock_options_cls),
        ):
            events = []
            async for event in runtime.start_turn(
                "sess_1", TurnInput(prompt="test")
            ):
                events.append(event)

            # 第一个是 TurnStarted，第二个是 TurnFailed
            assert isinstance(events[0], TurnStartedEvent)
            assert isinstance(events[1], TurnFailedEvent)
            assert "Missing required config" in events[1].error

    @pytest.mark.asyncio
    async def test_sdk_import_failure_yields_turn_failed(
        self,
        runtime: ClaudeAgentSdkRuntime,
    ):
        """SDK 未安装时 start_turn 应 yield TurnFailedEvent。"""

        def _fail_import():
            raise ImportError("claude-agent-sdk not found")

        with patch(
            "agent.control_plane.runtimes.claude_agent_sdk_runtime._import_sdk",
            side_effect=_fail_import,
        ):
            events = []
            async for event in runtime.start_turn(
                "sess_1", TurnInput(prompt="test")
            ):
                events.append(event)

            assert isinstance(events[0], TurnStartedEvent)
            assert isinstance(events[1], TurnFailedEvent)
            assert "claude-agent-sdk" in events[1].error

    @pytest.mark.asyncio
    async def test_subprocess_error_yields_turn_failed(
        self,
        runtime: ClaudeAgentSdkRuntime,
        mock_sdk_classes: tuple,
    ):
        """子进程异常退出应 yield TurnFailedEvent。"""
        mock_client_cls, mock_options_cls, mock_client = mock_sdk_classes
        mock_client.__aenter__ = AsyncMock(side_effect=RuntimeError("subprocess crashed"))

        with patch(
            "agent.control_plane.runtimes.claude_agent_sdk_runtime._import_sdk",
            return_value=(mock_client_cls, mock_options_cls),
        ):
            events = []
            async for event in runtime.start_turn(
                "sess_1", TurnInput(prompt="test")
            ):
                events.append(event)

            terminal = events[-1]
            assert isinstance(terminal, TurnFailedEvent)
            assert "subprocess crashed" in terminal.error


# ─── interrupt_turn 测试 ─────────────────────────────────────


class TestInterruptTurn:
    """验证 interrupt_turn 通过 asyncio.Event 通知终止。"""

    @pytest.mark.asyncio
    async def test_sets_abort_event(self, runtime: ClaudeAgentSdkRuntime):
        """interrupt_turn 应设置对应的 abort event。"""
        turn_id = "turn_test_123"
        abort_event = asyncio.Event()
        runtime._abort_events[turn_id] = abort_event

        await runtime.interrupt_turn(turn_id)

        assert abort_event.is_set()

    @pytest.mark.asyncio
    async def test_noop_for_unknown_turn(self, runtime: ClaudeAgentSdkRuntime):
        """对未知 turn_id，interrupt_turn 不应抛异常。"""
        await runtime.interrupt_turn("nonexistent_turn")


# ─── steer_turn 测试 ─────────────────────────────────────────


class TestSteerTurn:
    """验证 steer_turn 静默忽略（Claude SDK 不支持）。"""

    @pytest.mark.asyncio
    async def test_steer_turn_is_noop(self, runtime: ClaudeAgentSdkRuntime):
        await runtime.steer_turn("turn_1", "some input")


# ─── resume_session 测试 ─────────────────────────────────────


class TestResumeSession:
    """验证 resume_session 返回正确的 SessionRef。"""

    @pytest.mark.asyncio
    async def test_resume_returns_ref(self, runtime: ClaudeAgentSdkRuntime):
        ref = await runtime.resume_session("existing_sdk_session_id")
        assert isinstance(ref, SessionRef)
        assert ref.provider_session_id == "existing_sdk_session_id"
        assert ref.provider == "claude"


# ─── resolve_approval 测试 ───────────────────────────────────


class TestResolveApproval:
    """验证 resolve_approval 不抛异常。"""

    @pytest.mark.asyncio
    async def test_resolve_approval_noop(self, runtime: ClaudeAgentSdkRuntime):
        decision = ApprovalDecision(
            request_id="apr_123",
            decision="approved",
            reason="ok",
        )
        await runtime.resolve_approval(decision)


# ─── health_check 测试 ───────────────────────────────────────


class TestHealthCheck:
    """验证 health_check 检查 config 完整性和 SDK 可用性。"""

    @pytest.mark.asyncio
    async def test_healthy_when_config_complete_and_sdk_available(
        self, runtime: ClaudeAgentSdkRuntime
    ):
        """config 完整 + SDK 可用 → available=True。"""
        with patch(
            "agent.control_plane.runtimes.claude_agent_sdk_runtime._import_sdk",
            return_value=(MagicMock(), MagicMock()),
        ):
            status = await runtime.health_check()

            assert isinstance(status, HealthStatus)
            assert status.available is True
            assert "claude-opus-4-6" in status.message

    @pytest.mark.asyncio
    async def test_unhealthy_when_api_key_missing(self):
        """缺少 api_key → available=False，提示哪项缺失。"""
        rt = ClaudeAgentSdkRuntime(config={"model": "test"})
        status = await rt.health_check()

        assert status.available is False
        assert "api_key" in status.message

    @pytest.mark.asyncio
    async def test_unhealthy_when_model_missing(self):
        """缺少 model → available=False，提示哪项缺失。"""
        rt = ClaudeAgentSdkRuntime(config={"api_key": "sk-xxx"})
        status = await rt.health_check()

        assert status.available is False
        assert "model" in status.message

    @pytest.mark.asyncio
    async def test_unhealthy_when_both_missing(self):
        """同时缺少 api_key 和 model → 两项都提示。"""
        rt = ClaudeAgentSdkRuntime(config={})
        status = await rt.health_check()

        assert status.available is False
        assert "api_key" in status.message
        assert "model" in status.message

    @pytest.mark.asyncio
    async def test_unhealthy_when_sdk_not_installed(
        self, runtime: ClaudeAgentSdkRuntime
    ):
        """SDK 未安装 → available=False。"""

        def _fail_import():
            raise ImportError("no module")

        with patch(
            "agent.control_plane.runtimes.claude_agent_sdk_runtime._import_sdk",
            side_effect=_fail_import,
        ):
            status = await runtime.health_check()

            assert status.available is False
            assert "claude-agent-sdk" in status.message

    @pytest.mark.asyncio
    async def test_health_check_mentions_endpoint(
        self, runtime: ClaudeAgentSdkRuntime
    ):
        """健康时 message 应包含 endpoint 信息。"""
        with patch(
            "agent.control_plane.runtimes.claude_agent_sdk_runtime._import_sdk",
            return_value=(MagicMock(), MagicMock()),
        ):
            status = await runtime.health_check()
            assert "cpass.cc" in status.message


# ─── close 测试 ──────────────────────────────────────────────


class TestClose:
    """验证 close 清理资源。"""

    def test_close_clears_sessions(self, runtime: ClaudeAgentSdkRuntime):
        runtime._sessions["test"] = "val"
        runtime._abort_events["test"] = asyncio.Event()

        runtime.close()

        assert runtime._sessions == {}
        assert runtime._abort_events == {}
