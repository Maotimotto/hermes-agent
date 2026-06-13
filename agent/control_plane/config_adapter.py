"""Wave 9 — Hermes config → runtime_configs 适配器。

把 ``~/.hermes/config.yaml`` 的内容映射到
``RuntimeFactory.build_default_runtimes`` 期望的 dict 形状：

    {
      "claude": {"api_key": ..., "model": ..., "base_url": ...},
      "codex":  {"codex_bin": ..., "codex_home": ..., "permission_profile": ...},
    }

读取规则：

  Claude（一定要传，Daemon 启动需要）：
    - 优先级：cfg["providers"]["claude"] > cfg["model"] > cfg["providers"]["custom"]
    - 必须包含 api_key 和 model；任一缺失则不构造 claude runtime

  Codex（可选）：
    - 优先级：cfg["providers"]["codex"] > cfg["control_plane"]["codex"]
    - 任意字段可缺；缺则用 builder 的默认值

兼容性：当 hermes config 未提供任何字段时，返回空 dict（daemon 不注册 runtime，
保持单元测试模式）。
"""

from __future__ import annotations

from typing import Any


# ── 工具：从优先级链取值 ─────────────────────────────────────────────────────


def _first_present(*sources: dict[str, Any] | None, key: str) -> Any | None:
    for src in sources:
        if isinstance(src, dict) and src.get(key) not in (None, ""):
            return src[key]
    return None


# ── Claude config 提取 ──────────────────────────────────────────────────────


def _extract_claude_config(cfg: dict[str, Any]) -> dict[str, Any] | None:
    providers = cfg.get("providers") or {}
    claude_section = providers.get("claude") if isinstance(providers, dict) else None
    custom_section = providers.get("custom") if isinstance(providers, dict) else None
    top_model = cfg.get("model") or {}

    api_key = _first_present(claude_section, top_model, custom_section, key="api_key")
    base_url = _first_present(claude_section, top_model, custom_section, key="base_url")

    # model 字段优先级：claude.model > top.model.default > custom.model[0] > custom.model
    model = None
    if isinstance(claude_section, dict):
        model = claude_section.get("model")
    if not model and isinstance(top_model, dict):
        model = top_model.get("default") or top_model.get("model")
    if not model and isinstance(custom_section, dict):
        m = custom_section.get("model")
        if isinstance(m, list) and m:
            model = m[0]
        elif isinstance(m, str):
            model = m

    if not api_key or not model:
        return None

    out: dict[str, Any] = {"api_key": api_key, "model": model}
    if base_url:
        out["base_url"] = base_url
    return out


# ── Codex config 提取 ───────────────────────────────────────────────────────


def _extract_codex_config(cfg: dict[str, Any]) -> dict[str, Any] | None:
    providers = cfg.get("providers") or {}
    codex_section = providers.get("codex") if isinstance(providers, dict) else None
    cp_codex = (cfg.get("control_plane") or {}).get("codex") if isinstance(
        cfg.get("control_plane"), dict
    ) else None

    out: dict[str, Any] = {}
    for key in ("codex_bin", "codex_home", "permission_profile"):
        val = _first_present(codex_section, cp_codex, key=key)
        if val is not None:
            out[key] = val

    # codex 是可选的：即使全部字段都缺，daemon 仍可只跑 claude
    return out or None


# ── 顶层入口 ────────────────────────────────────────────────────────────────


def build_runtime_configs_from_hermes_config(
    cfg: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """从 hermes config dict 构造 runtime_configs。

    返回形状直接喂给 ``mount_to(runtime_configs=...)`` 或
    ``create_control_plane_app(runtime_configs=...)``。
    """
    out: dict[str, dict[str, Any]] = {}
    claude = _extract_claude_config(cfg)
    if claude:
        out["claude"] = claude
    codex = _extract_codex_config(cfg)
    if codex:
        out["codex"] = codex
    return out


def load_runtime_configs() -> dict[str, dict[str, Any]]:
    """便利函数：load_config() + build_runtime_configs_from_hermes_config。

    适合在 daemon 主进程入口直接调，无需手动 import hermes_cli。
    """
    try:
        from hermes_cli.config import load_config
    except ImportError:  # pragma: no cover
        return {}
    return build_runtime_configs_from_hermes_config(load_config())


__all__ = [
    "build_runtime_configs_from_hermes_config",
    "load_runtime_configs",
]
