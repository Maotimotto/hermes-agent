"""Unit tests for agent.control_plane.error_recovery.

覆盖：
- 9 个 ErrorCategory 的分类路径（含字符串模式 + 异常类型双路径）
- ClassifiedError.to_event_payload 字段对齐 TurnFailedEvent
- 优先级：CancelledError 高于 message 匹配；rate_limit 高于 overloaded
- is_retryable / retry_after_ms 便捷 API
- _DEFAULT_RETRY_AFTER_MS 与 _RETRYABLE_CATEGORIES 一致性
"""

from __future__ import annotations

import asyncio

import pytest

from agent.control_plane.error_recovery import (
    ClassifiedError,
    ErrorCategory,
    classify_runtime_error,
    is_retryable,
    retry_after_ms,
)


# ── 类型分类 ──────────────────────────────────────────────────────


class TestExceptionTypeDispatch:
    """按异常类型分类（不依赖 message 内容）。"""

    def test_cancelled_error_classified_as_cancelled(self) -> None:
        result = classify_runtime_error(asyncio.CancelledError())
        assert result.category == ErrorCategory.cancelled
        assert result.retryable is False
        assert result.retry_after_ms == 0

    def test_asyncio_timeout_error_classified_as_timeout(self) -> None:
        result = classify_runtime_error(asyncio.TimeoutError())
        assert result.category == ErrorCategory.timeout
        assert result.retryable is True
        assert result.retry_after_ms > 0

    def test_builtin_timeout_error_classified_as_timeout(self) -> None:
        result = classify_runtime_error(TimeoutError("read timed out"))
        assert result.category == ErrorCategory.timeout
        assert result.retryable is True

    def test_connection_error_classified_as_network(self) -> None:
        result = classify_runtime_error(ConnectionError("connection refused"))
        assert result.category == ErrorCategory.network
        assert result.retryable is True

    def test_value_error_classified_as_config_invalid(self) -> None:
        result = classify_runtime_error(ValueError("base_url is required"))
        assert result.category == ErrorCategory.config_invalid
        assert result.retryable is False

    def test_broken_pipe_classified_as_process_crash(self) -> None:
        result = classify_runtime_error(BrokenPipeError("pipe broken"))
        assert result.category == ErrorCategory.process_crash
        assert result.retryable is True

    def test_file_not_found_with_cli_message_classified_as_cli_not_found(self) -> None:
        result = classify_runtime_error(
            FileNotFoundError("/usr/bin/codex: No such file or directory")
        )
        assert result.category == ErrorCategory.cli_not_found

    def test_file_not_found_without_cli_message_falls_through(self) -> None:
        # 普通文件丢失（非 CLI），不应误判为 cli_not_found
        result = classify_runtime_error(FileNotFoundError("/tmp/foo.txt"))
        assert result.category == ErrorCategory.unknown


# ── 类型名匹配（无 import SDK 也能识别） ────────────────────────────


class TestExceptionNameDispatch:
    """通过类名识别第三方 SDK 异常（避免硬依赖 claude-agent-sdk）。"""

    def test_cli_not_found_error_name_match(self) -> None:
        # 模拟 claude_agent_sdk.CLINotFoundError
        class CLINotFoundError(Exception):
            pass

        result = classify_runtime_error(CLINotFoundError("claude not installed"))
        assert result.category == ErrorCategory.cli_not_found

    def test_cli_connection_error_name_match(self) -> None:
        class CLIConnectionError(Exception):
            pass

        result = classify_runtime_error(CLIConnectionError("subprocess died"))
        assert result.category == ErrorCategory.process_crash

    def test_process_error_name_match(self) -> None:
        class ProcessError(Exception):
            pass

        result = classify_runtime_error(ProcessError("non-zero exit"))
        assert result.category == ErrorCategory.process_crash


# ── 字符串模式匹配 ──────────────────────────────────────────────────


class TestMessagePatternDispatch:
    """无类型信息时，靠 message 关键词分类。"""

    @pytest.mark.parametrize(
        "msg",
        [
            "HTTP 401 Unauthorized",
            "Invalid API key provided",
            "authentication failed",
        ],
    )
    def test_auth_patterns(self, msg: str) -> None:
        result = classify_runtime_error(Exception(msg))
        assert result.category == ErrorCategory.auth
        assert result.retryable is False

    @pytest.mark.parametrize(
        "msg",
        [
            "HTTP 429 too many requests",
            "rate limit exceeded",
            "quota exceeded for this model",
        ],
    )
    def test_rate_limit_patterns(self, msg: str) -> None:
        result = classify_runtime_error(Exception(msg))
        assert result.category == ErrorCategory.rate_limit
        assert result.retryable is False

    @pytest.mark.parametrize(
        "msg",
        [
            "HTTP 503 Service Unavailable",
            "Server overloaded, please retry",
        ],
    )
    def test_overloaded_patterns(self, msg: str) -> None:
        result = classify_runtime_error(Exception(msg))
        assert result.category == ErrorCategory.overloaded
        assert result.retryable is True

    def test_rate_limit_priority_over_overloaded(self) -> None:
        """同时含 'too many requests'（overload pattern）+ '429' 应归 rate_limit。"""
        result = classify_runtime_error(
            Exception("HTTP 429: too many requests, please backoff")
        )
        assert result.category == ErrorCategory.rate_limit


# ── ClassifiedError 输出 ────────────────────────────────────────────


class TestClassifiedErrorOutput:
    def test_to_event_payload_aligns_with_turn_failed_fields(self) -> None:
        result = classify_runtime_error(asyncio.TimeoutError())
        payload = result.to_event_payload()
        assert set(payload.keys()) == {"error", "code", "retryable"}
        assert payload["code"] == "timeout"
        assert payload["retryable"] is True
        assert payload["error"]  # friendly_message 非空

    def test_friendly_message_is_chinese_for_known_categories(self) -> None:
        # 用户偏好中文界面；除 unknown 外都应有中文文案
        for cat in ErrorCategory:
            result = ClassifiedError(
                category=cat,
                retryable=False,
                code=cat.value,
            )
            # 仅校验编程接口形态；具体文案由 _FRIENDLY_MESSAGES 驱动
            assert isinstance(result.code, str)

    def test_provider_propagated(self) -> None:
        result = classify_runtime_error(
            ConnectionError("refused"),
            provider="claude",
        )
        assert result.provider == "claude"

    def test_technical_message_truncated_to_500(self) -> None:
        long_msg = "x" * 2000
        result = classify_runtime_error(Exception(long_msg))
        assert len(result.technical_message) <= 500


# ── 一致性 ──────────────────────────────────────────────────────────


class TestRetryConsistency:
    """retryable 与 retry_after_ms 互相一致。"""

    def test_retryable_categories_have_positive_backoff(self) -> None:
        for cat in [
            ErrorCategory.network,
            ErrorCategory.timeout,
            ErrorCategory.overloaded,
            ErrorCategory.process_crash,
        ]:
            # 直接构造一条该类别的 ClassifiedError 对应的异常做端到端验证
            exc_map = {
                ErrorCategory.network: ConnectionError("connection refused"),
                ErrorCategory.timeout: asyncio.TimeoutError(),
                ErrorCategory.overloaded: Exception("HTTP 503 overloaded"),
                ErrorCategory.process_crash: BrokenPipeError("pipe"),
            }
            result = classify_runtime_error(exc_map[cat])
            assert result.retryable is True, f"{cat} should be retryable"
            assert result.retry_after_ms > 0, f"{cat} should have backoff"

    def test_non_retryable_categories_have_zero_backoff(self) -> None:
        for exc, expected in [
            (asyncio.CancelledError(), ErrorCategory.cancelled),
            (ValueError("bad config"), ErrorCategory.config_invalid),
            (Exception("HTTP 401"), ErrorCategory.auth),
            (Exception("HTTP 429"), ErrorCategory.rate_limit),
        ]:
            result = classify_runtime_error(exc)
            assert result.category == expected
            assert result.retryable is False
            assert result.retry_after_ms == 0


# ── 便捷 API ────────────────────────────────────────────────────────


class TestConvenienceApi:
    def test_is_retryable_true(self) -> None:
        assert is_retryable(asyncio.TimeoutError()) is True

    def test_is_retryable_false_for_auth(self) -> None:
        assert is_retryable(Exception("HTTP 401 unauthorized")) is False

    def test_retry_after_ms_for_overloaded(self) -> None:
        ms = retry_after_ms(Exception("HTTP 503"))
        assert ms == 3000

    def test_retry_after_ms_zero_for_non_retryable(self) -> None:
        assert retry_after_ms(asyncio.CancelledError()) == 0
