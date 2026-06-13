"""Wave 9 — Hermes config → runtime_configs 适配器测试。"""

from __future__ import annotations

import pytest

from agent.control_plane.config_adapter import (
    build_runtime_configs_from_hermes_config,
)


# ── Claude 提取 ──────────────────────────────────────────────────────────────


class TestClaudeExtraction:
    def test_explicit_claude_section_wins(self):
        cfg = {
            "providers": {
                "claude": {
                    "api_key": "sk-claude",
                    "model": "claude-opus-4",
                    "base_url": "https://api.anthropic.com",
                },
                "custom": {
                    "api_key": "sk-custom",
                    "model": "wrong",
                    "base_url": "https://x.com",
                },
            },
            "model": {"default": "ignored", "api_key": "sk-top"},
        }
        out = build_runtime_configs_from_hermes_config(cfg)
        assert out["claude"] == {
            "api_key": "sk-claude",
            "model": "claude-opus-4",
            "base_url": "https://api.anthropic.com",
        }

    def test_falls_back_to_top_model(self):
        cfg = {
            "model": {
                "default": "claude-opus-4-7",
                "api_key": "sk-top",
                "base_url": "https://www.cpass.cc",
            },
        }
        out = build_runtime_configs_from_hermes_config(cfg)
        assert out["claude"] == {
            "api_key": "sk-top",
            "model": "claude-opus-4-7",
            "base_url": "https://www.cpass.cc",
        }

    def test_falls_back_to_custom_provider_with_model_list(self):
        cfg = {
            "providers": {
                "custom": {
                    "api_key": "sk-c",
                    "model": ["claude-opus-4-7", "claude-sonnet-4-6"],
                    "base_url": "https://www.cpass.cc",
                },
            },
        }
        out = build_runtime_configs_from_hermes_config(cfg)
        assert out["claude"]["model"] == "claude-opus-4-7"

    def test_missing_api_key_drops_claude(self):
        cfg = {"model": {"default": "claude-opus-4"}}
        out = build_runtime_configs_from_hermes_config(cfg)
        assert "claude" not in out

    def test_missing_model_drops_claude(self):
        cfg = {"model": {"api_key": "sk-x"}}
        out = build_runtime_configs_from_hermes_config(cfg)
        assert "claude" not in out

    def test_no_base_url_omitted(self):
        cfg = {
            "providers": {"claude": {"api_key": "sk-c", "model": "m"}},
        }
        out = build_runtime_configs_from_hermes_config(cfg)
        assert "base_url" not in out["claude"]


# ── Codex 提取 ──────────────────────────────────────────────────────────────


class TestCodexExtraction:
    def test_explicit_codex_section(self):
        cfg = {
            "providers": {
                "codex": {
                    "codex_bin": "/usr/local/bin/codex",
                    "codex_home": "/home/user/.codex",
                    "permission_profile": "full-access",
                },
            },
        }
        out = build_runtime_configs_from_hermes_config(cfg)
        assert out["codex"] == {
            "codex_bin": "/usr/local/bin/codex",
            "codex_home": "/home/user/.codex",
            "permission_profile": "full-access",
        }

    def test_control_plane_codex_fallback(self):
        cfg = {
            "control_plane": {
                "codex": {"codex_bin": "/cp/codex"},
            },
        }
        out = build_runtime_configs_from_hermes_config(cfg)
        assert out["codex"] == {"codex_bin": "/cp/codex"}

    def test_no_codex_section_omitted(self):
        cfg = {"providers": {"claude": {"api_key": "k", "model": "m"}}}
        out = build_runtime_configs_from_hermes_config(cfg)
        assert "codex" not in out


# ── 综合 ────────────────────────────────────────────────────────────────────


class TestCombined:
    def test_both_present(self):
        cfg = {
            "providers": {
                "claude": {"api_key": "k1", "model": "m1"},
                "codex": {"codex_bin": "/x"},
            },
        }
        out = build_runtime_configs_from_hermes_config(cfg)
        assert set(out.keys()) == {"claude", "codex"}

    def test_empty_returns_empty(self):
        out = build_runtime_configs_from_hermes_config({})
        assert out == {}

    def test_real_world_shape(self):
        """模拟用户当前 ~/.hermes/config.yaml 实际形状。"""
        cfg = {
            "model": {
                "default": "claude-opus-4-7",
                "provider": "custom",
                "api_mode": "anthropic_messages",
                "base_url": "https://www.cpass.cc",
                "api_key": "sk-UbS...a2aF",
            },
            "providers": {
                "custom": {
                    "base_url": "https://www.cpass.cc",
                    "api_key": "sk-UbS...a2aF",
                    "model": ["claude-opus-4-7", "claude-sonnet-4-6"],
                },
            },
        }
        out = build_runtime_configs_from_hermes_config(cfg)
        assert out["claude"]["model"] == "claude-opus-4-7"
        assert out["claude"]["api_key"].startswith("sk-")
        assert out["claude"]["base_url"] == "https://www.cpass.cc"


# ── load_runtime_configs（集成）─────────────────────────────────────────────


class TestLoadRuntimeConfigs:
    def test_load_runs_against_real_hermes_config(self):
        """跑用户真实 ~/.hermes/config.yaml。
        不断言具体值（防泄漏），只验证形状。"""
        from agent.control_plane.config_adapter import load_runtime_configs

        out = load_runtime_configs()
        assert isinstance(out, dict)
        # 当前真实 config 至少能拿到 claude
        if "claude" in out:
            assert "api_key" in out["claude"]
            assert "model" in out["claude"]
