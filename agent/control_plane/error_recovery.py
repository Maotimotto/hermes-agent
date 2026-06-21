"""Control-plane 错误恢复：错误分类 + 友好提示 + 重试策略。

V1.1 P1 「基础错误恢复」模块。**不复用** agent/error_classifier.py（1365 行）：

- 老分类器服务于 Anthropic/OpenAI 直连 API 的 main retry loop（HTTP 状态码 +
  provider-specific JSON），是为「换 key/换 provider/压上下文」设计的高维决策。
- control-plane 这边面对的是 Codex 子进程 + Claude SDK 子进程的运行时异常
  （CLINotFoundError / ProcessError / asyncio.TimeoutError / ConnectionError /
  ValueError），决策面只有「重试一次 / 不重试 / 给用户友好提示」三档。
- 共用一份分类器会把两套语义搅在一起，且 control-plane 不允许依赖 agent/ 主流程。

设计要点：
1. 类别枚举 8 类，覆盖 P1 清单 4 条（turn 失败重试 / provider 不可用提示 /
   网络重连 / 子进程崩溃重启）。
2. 分类结果带 retry_after_ms：上层重试器直接用，无需再决策。
3. friendly_message 中文，给前端 ErrorBanner 直接展示（用户偏好中文界面）。
4. classify_runtime_error 是纯函数：异常 → ClassifiedError，方便单测。
"""

from __future__ import annotations

import asyncio
import enum
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── 错误类别 ────────────────────────────────────────────────────────


class ErrorCategory(str, enum.Enum):
    """运行时错误类别。每一类对应一种恢复策略。"""

    # 网络类 — 可重试
    network = "network"              # 连接拒绝、DNS 失败、连接重置
    timeout = "timeout"              # 读/写/连接超时
    overloaded = "overloaded"        # 5xx / 503 / 服务过载

    # 认证 / 配额 — 不应自动重试
    auth = "auth"                    # 401/403、API key 无效
    rate_limit = "rate_limit"        # 429、配额耗尽

    # 子进程类
    process_crash = "process_crash"  # 子进程异常退出（Codex CLI / Claude SDK 子进程）
    cli_not_found = "cli_not_found"  # 二进制不存在或 PATH 找不到

    # 用户配置 / payload — 不重试，提示修配置
    config_invalid = "config_invalid"  # ValueError、配置缺字段、模型不存在
    cancelled = "cancelled"            # 用户主动取消
    unknown = "unknown"                # 兜底


# ── 分类结果 ───────────────────────────────────────────────────────


@dataclass
class ClassifiedError:
    """错误分类结果 + 恢复策略 hints。"""

    category: ErrorCategory
    retryable: bool
    retry_after_ms: int = 0          # 0 = 立即重试；>0 = 退避等待
    code: str = ""                    # 机器可读 error code（前端区分用）
    friendly_message: str = ""        # 给用户看的中文提示
    technical_message: str = ""       # 给开发看的英文原文（截 500 字）
    provider: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_event_payload(self) -> dict[str, Any]:
        """转成 turn.failed 事件的 payload（已对齐 TurnFailedEvent 字段）。"""
        return {
            "error": self.friendly_message or self.technical_message,
            "code": self.code,
            "retryable": self.retryable,
        }


# ── 关键字模式 ─────────────────────────────────────────────────────


# 注意：这些模式都是 lower-case 后匹配（_classify_by_message 会先 .lower()）
_NETWORK_PATTERNS = (
    "connection refused",
    "connection reset",
    "connection aborted",
    "name or service not known",
    "no route to host",
    "network is unreachable",
    "broken pipe",
    "connection closed",
)

_TIMEOUT_PATTERNS = (
    "timed out",
    "timeout",
    "read timeout",
    "operation timed out",
)

_OVERLOAD_PATTERNS = (
    "503",
    "overloaded",
    "service unavailable",
    "server is busy",
    "too many requests",  # 注：429 也含此串，但 429 优先归 rate_limit
)

_AUTH_PATTERNS = (
    "401",
    "403",
    "unauthorized",
    "authentication failed",
    "invalid api key",
    "missing api key",
    "api_key",
    "auth_token",
)

_RATE_LIMIT_PATTERNS = (
    "429",
    "rate limit",
    "quota exceeded",
    "rate_limit_error",
)

_CLI_NOT_FOUND_PATTERNS = (
    "no such file or directory",
    "command not found",
    "executable not found",
    "is not installed",
    "claude code requires",  # claude SDK 报错
)

_PROCESS_CRASH_PATTERNS = (
    "process exited",
    "subprocess returned",
    "non-zero exit",
    "broken pipe",  # 子进程提前关 stdin/stdout
    "eof when reading",
)


# ── 友好提示模板 ───────────────────────────────────────────────────


_FRIENDLY_MESSAGES = {
    ErrorCategory.network: "网络连接异常，已自动重试一次仍失败。请检查网络或代理配置后再发起 turn。",
    ErrorCategory.timeout: "请求超时。可能是模型响应慢或网络抖动，稍后可重试。",
    ErrorCategory.overloaded: "Provider 服务过载，请稍后重试。",
    ErrorCategory.auth: "认证失败：API key 无效或已过期。请到设置页更新凭证。",
    ErrorCategory.rate_limit: "触发速率限制，请稍后重试或检查账户配额。",
    ErrorCategory.process_crash: "Provider 子进程异常退出。已自动重启，请重新发起 turn。",
    ErrorCategory.cli_not_found: "Provider 二进制未找到。请检查 codex/claude 安装路径，或到设置页修改 codex_bin。",
    ErrorCategory.config_invalid: "Provider 配置无效。请检查 model / base_url / API key 字段是否填写正确。",
    ErrorCategory.cancelled: "Turn 已取消。",
    ErrorCategory.unknown: "未知错误。可在事件流查看技术细节，或重试一次。",
}


_DEFAULT_RETRY_AFTER_MS = {
    ErrorCategory.network: 1_500,
    ErrorCategory.timeout: 2_000,
    ErrorCategory.overloaded: 3_000,
    ErrorCategory.process_crash: 1_000,
    # 不重试的类别 retry_after 留 0
    ErrorCategory.auth: 0,
    ErrorCategory.rate_limit: 0,
    ErrorCategory.cli_not_found: 0,
    ErrorCategory.config_invalid: 0,
    ErrorCategory.cancelled: 0,
    ErrorCategory.unknown: 0,
}


_RETRYABLE_CATEGORIES = {
    ErrorCategory.network,
    ErrorCategory.timeout,
    ErrorCategory.overloaded,
    ErrorCategory.process_crash,
}


# ── 主分类函数 ─────────────────────────────────────────────────────


def classify_runtime_error(
    exc: BaseException,
    *,
    provider: str | None = None,
) -> ClassifiedError:
    """把 runtime 抛的异常分类为 ClassifiedError。

    分类优先级（高 → 低）：
    1. asyncio.CancelledError → cancelled
    2. 子进程类异常类型 → process_crash / cli_not_found
    3. asyncio.TimeoutError / TimeoutError → timeout
    4. ConnectionError → network
    5. ValueError（配置错） → config_invalid
    6. 字符串模式匹配（HTTP 状态码、provider error message）
    7. 兜底 unknown

    类型优先于字符串：HTTP 401 在异常 message 里时仍走字符串匹配，但
    asyncio.TimeoutError 会立刻定位到 timeout 不再扫描 message。
    """
    msg = str(exc)
    msg_lower = msg.lower()
    technical = msg[:500]

    # 1) 取消
    if isinstance(exc, asyncio.CancelledError):
        return _build(
            ErrorCategory.cancelled,
            provider=provider,
            technical=technical,
        )

    # 2) 子进程类（按异常类名识别，避免硬依赖 SDK 类型）
    exc_type = type(exc).__name__
    if exc_type in {"CLINotFoundError", "FileNotFoundError"}:
        # FileNotFoundError 也可能是工作区文件丢失 — 用 message 二次确认
        if exc_type == "FileNotFoundError" and not any(
            p in msg_lower for p in _CLI_NOT_FOUND_PATTERNS
        ):
            # 不是 CLI 缺失，可能是 git worktree 文件丢失 → unknown
            pass
        else:
            return _build(
                ErrorCategory.cli_not_found,
                provider=provider,
                technical=technical,
            )
    if exc_type in {"CLIConnectionError", "ProcessError", "BrokenPipeError"}:
        return _build(
            ErrorCategory.process_crash,
            provider=provider,
            technical=technical,
        )

    # 3) 超时
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return _build(
            ErrorCategory.timeout,
            provider=provider,
            technical=technical,
        )

    # 4) 网络
    if isinstance(exc, ConnectionError):
        return _build(
            ErrorCategory.network,
            provider=provider,
            technical=technical,
        )

    # 5) ValueError 一般是 control-plane 自己的配置校验抛的
    if isinstance(exc, ValueError):
        return _build(
            ErrorCategory.config_invalid,
            provider=provider,
            technical=technical,
        )

    # 6) 字符串模式匹配 — 顺序敏感（rate_limit 早于 overloaded）
    if _match_any(msg_lower, _RATE_LIMIT_PATTERNS):
        return _build(ErrorCategory.rate_limit, provider=provider, technical=technical)
    if _match_any(msg_lower, _AUTH_PATTERNS):
        return _build(ErrorCategory.auth, provider=provider, technical=technical)
    if _match_any(msg_lower, _OVERLOAD_PATTERNS):
        return _build(ErrorCategory.overloaded, provider=provider, technical=technical)
    if _match_any(msg_lower, _TIMEOUT_PATTERNS):
        return _build(ErrorCategory.timeout, provider=provider, technical=technical)
    if _match_any(msg_lower, _NETWORK_PATTERNS):
        return _build(ErrorCategory.network, provider=provider, technical=technical)
    if _match_any(msg_lower, _CLI_NOT_FOUND_PATTERNS):
        return _build(
            ErrorCategory.cli_not_found,
            provider=provider,
            technical=technical,
        )
    if _match_any(msg_lower, _PROCESS_CRASH_PATTERNS):
        return _build(
            ErrorCategory.process_crash,
            provider=provider,
            technical=technical,
        )

    # 7) 兜底
    return _build(ErrorCategory.unknown, provider=provider, technical=technical)


# ── 辅助函数 ───────────────────────────────────────────────────────


def _match_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(p in text for p in patterns)


def _build(
    category: ErrorCategory,
    *,
    provider: str | None,
    technical: str,
) -> ClassifiedError:
    return ClassifiedError(
        category=category,
        retryable=category in _RETRYABLE_CATEGORIES,
        retry_after_ms=_DEFAULT_RETRY_AFTER_MS.get(category, 0),
        code=category.value,
        friendly_message=_FRIENDLY_MESSAGES.get(category, ""),
        technical_message=technical,
        provider=provider,
    )


# ── 便捷 API ───────────────────────────────────────────────────────


def is_retryable(exc: BaseException, *, provider: str | None = None) -> bool:
    """快捷查询：异常是否应当重试。等价于 classify_runtime_error(exc).retryable。"""
    return classify_runtime_error(exc, provider=provider).retryable


def retry_after_ms(exc: BaseException, *, provider: str | None = None) -> int:
    """快捷查询：建议的重试退避毫秒数。"""
    return classify_runtime_error(exc, provider=provider).retry_after_ms
