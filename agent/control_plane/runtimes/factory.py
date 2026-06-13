"""Runtime factory — 统一构造 AgentRuntime 实例并注入依赖。

Wave 8：把 ApprovalGate / Store / 配置 字典化注入到 runtime 实例化路径上。
Daemon 启动时调用 ``build_default_runtimes(state)`` 一次性把所有 runtime
注册进 RuntimeRegistry，避免在多处散落 ``CodexAppServerRuntime(...)`` /
``ClaudeAgentSdkRuntime(...)`` 调用。

接口约定：
  build_runtime(kind, *, store, approval_gate, config) -> AgentRuntime
  build_default_runtimes(state) -> dict[kind, AgentRuntime]

测试可注入 ``runtime_overrides=`` 替换某 kind 的工厂实现（用于注入 mock）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from agent.control_plane.approval import ApprovalGate
from agent.control_plane.runtimes.interface import AgentRuntime
from agent.control_plane.store import SessionStore

logger = logging.getLogger(__name__)


# ── 单 runtime 构造器 ────────────────────────────────────────────────────────


def _build_codex_runtime(
    *,
    store: SessionStore,
    approval_gate: ApprovalGate | None,
    config: dict[str, Any],
) -> AgentRuntime:
    """构造 CodexAppServerRuntime。

    config 字段（与现有 CodexAppServerRuntime.__init__ 对齐）：
      - codex_bin: str = "codex"
      - codex_home: str | None
      - permission_profile: str | None
    """
    from agent.control_plane.runtimes.codex_app_server_runtime import (
        CodexAppServerRuntime,
    )

    return CodexAppServerRuntime(
        store=store,
        codex_bin=config.get("codex_bin", "codex"),
        codex_home=config.get("codex_home"),
        permission_profile=config.get("permission_profile"),
        approval_gate=approval_gate,
    )


def _build_claude_runtime(
    *,
    store: SessionStore,  # noqa: ARG001 — claude SDK runtime 不直接持 store
    approval_gate: ApprovalGate | None,
    config: dict[str, Any],
) -> AgentRuntime:
    """构造 ClaudeAgentSdkRuntime。

    config 字段（与现有 ClaudeAgentSdkRuntime.__init__ 对齐）：
      - api_key: str  (必填)
      - model: str    (必填)
      - base_url: str | None
    """
    from agent.control_plane.runtimes.claude_agent_sdk_runtime import (
        ClaudeAgentSdkRuntime,
    )

    return ClaudeAgentSdkRuntime(
        config=config,
        approval_gate=approval_gate,
    )


# ── 注册表 ───────────────────────────────────────────────────────────────────


RuntimeBuilder = Callable[..., AgentRuntime]

_DEFAULT_BUILDERS: dict[str, RuntimeBuilder] = {
    "codex": _build_codex_runtime,
    "claude": _build_claude_runtime,
}


def build_runtime(
    kind: str,
    *,
    store: SessionStore,
    approval_gate: ApprovalGate | None,
    config: dict[str, Any],
    overrides: dict[str, RuntimeBuilder] | None = None,
) -> AgentRuntime:
    """构造单个 runtime；overrides 让测试注入 mock 工厂。"""
    builders = {**_DEFAULT_BUILDERS, **(overrides or {})}
    builder = builders.get(kind)
    if builder is None:
        raise ValueError(
            f"unknown runtime kind={kind!r}; "
            f"known={sorted(builders.keys())}"
        )
    return builder(store=store, approval_gate=approval_gate, config=config)


def build_default_runtimes(
    *,
    store: SessionStore,
    approval_gate: ApprovalGate | None,
    runtime_configs: dict[str, dict[str, Any]],
    overrides: dict[str, RuntimeBuilder] | None = None,
) -> dict[str, AgentRuntime]:
    """批量构造所有可用 runtime。

    runtime_configs 形如：
        {
          "codex":  {"codex_bin": "codex", ...},
          "claude": {"api_key": "...", "model": "claude-opus-4", ...},
        }

    缺失的 kind 不会报错，只是不构造（允许部署只跑一个 provider）。
    """
    runtimes: dict[str, AgentRuntime] = {}
    for kind, cfg in runtime_configs.items():
        try:
            runtimes[kind] = build_runtime(
                kind,
                store=store,
                approval_gate=approval_gate,
                config=cfg,
                overrides=overrides,
            )
            logger.info(
                "[runtime_factory] built kind=%s gate=%s",
                kind, "yes" if approval_gate else "no",
            )
        except Exception:
            logger.exception(
                "[runtime_factory] failed to build runtime kind=%s; skipping",
                kind,
            )
    return runtimes


__all__ = [
    "build_runtime",
    "build_default_runtimes",
    "RuntimeBuilder",
]
