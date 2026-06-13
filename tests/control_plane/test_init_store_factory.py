"""Wave 8.2 — init_store 接入 RuntimeFactory 测试。

验证 create_control_plane_app(runtime_configs=...) 启动后
RuntimeRegistry 自动有 runtime，且 runtime 持有 ApprovalGate。
"""

from __future__ import annotations

import tempfile
import os

import pytest
from fastapi.testclient import TestClient

from gateway.control_plane import mount_to
from gateway.control_plane.app import create_control_plane_app
from gateway.control_plane.deps import AppState


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        yield path
    finally:
        if os.path.exists(path):
            os.unlink(path)


class TestInitStoreRuntimeRegistration:
    def test_no_runtime_configs_no_registration(self, db_path):
        """不传 runtime_configs → registry 为空。"""
        app = create_control_plane_app(db_path=db_path, runtime_configs=None)
        with TestClient(app) as client:
            client.get("/health")  # 触发 startup
            state: AppState = app.state.cp
            # registry 内部 _runtimes 应为空
            assert state.runtime_registry._runtimes == {}

    def test_runtime_configs_triggers_registration(self, db_path, monkeypatch):
        """传 runtime_configs → registry 注册了对应 kind。"""
        from agent.control_plane.runtimes import factory as factory_mod

        captured = {}

        def fake_build_default_runtimes(
            *, store, approval_gate, runtime_configs, overrides=None
        ):
            captured["store"] = store
            captured["gate"] = approval_gate
            captured["configs"] = runtime_configs
            # 返回假 runtime
            return {kind: f"runtime_{kind}" for kind in runtime_configs}

        monkeypatch.setattr(
            factory_mod, "build_default_runtimes", fake_build_default_runtimes
        )

        app = create_control_plane_app(
            db_path=db_path,
            runtime_configs={
                "codex": {},
                "claude": {"api_key": "sk", "model": "m"},
            },
        )
        with TestClient(app) as client:
            client.get("/health")
            state: AppState = app.state.cp
            assert "codex" in state.runtime_registry._runtimes
            assert "claude" in state.runtime_registry._runtimes
            assert state.runtime_registry._runtimes["codex"] == "runtime_codex"
            # 工厂被传入了 store 和 ApprovalGate
            assert captured["store"] is state.store
            assert captured["gate"] is state.approval_gate
            assert captured["configs"] == {
                "codex": {},
                "claude": {"api_key": "sk", "model": "m"},
            }

    def test_mount_to_passes_runtime_configs(self, db_path, monkeypatch):
        """mount_to 透传 runtime_configs 到 create_control_plane_app。"""
        from fastapi import FastAPI
        from gateway.control_plane import app as app_mod

        seen = {}
        original = app_mod.create_control_plane_app

        def spy(**kwargs):
            seen.update(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(app_mod, "create_control_plane_app", spy)

        main = FastAPI()
        cfg = {"claude": {"api_key": "k", "model": "m"}}
        mount_to(main, db_path=db_path, runtime_configs=cfg)
        assert seen["runtime_configs"] == cfg
        assert seen["db_path"] == db_path

    def test_mount_to_auto_loads_when_none(self, db_path, monkeypatch):
        """runtime_configs=None + auto_load_runtime_configs=True → 调用 load_runtime_configs。"""
        from fastapi import FastAPI
        from gateway.control_plane import app as app_mod
        from agent.control_plane import config_adapter

        loaded = {"claude": {"api_key": "auto-k", "model": "auto-m"}}
        monkeypatch.setattr(
            config_adapter, "load_runtime_configs", lambda: loaded
        )

        seen = {}
        original = app_mod.create_control_plane_app

        def spy(**kwargs):
            seen.update(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(app_mod, "create_control_plane_app", spy)

        main = FastAPI()
        mount_to(main, db_path=db_path)  # 不传 runtime_configs
        assert seen["runtime_configs"] == loaded

    def test_mount_to_auto_load_disabled(self, db_path, monkeypatch):
        """auto_load_runtime_configs=False → 不调用 load_runtime_configs。"""
        from fastapi import FastAPI
        from gateway.control_plane import app as app_mod
        from agent.control_plane import config_adapter

        called = {"n": 0}

        def fake_load():
            called["n"] += 1
            return {"claude": {"api_key": "k", "model": "m"}}

        monkeypatch.setattr(config_adapter, "load_runtime_configs", fake_load)

        seen = {}
        original = app_mod.create_control_plane_app

        def spy(**kwargs):
            seen.update(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(app_mod, "create_control_plane_app", spy)

        main = FastAPI()
        mount_to(main, db_path=db_path, auto_load_runtime_configs=False)
        assert called["n"] == 0
        assert seen["runtime_configs"] is None

    def test_mount_to_explicit_empty_disables_auto_load(self, db_path, monkeypatch):
        """显式 runtime_configs={} → 跳过 auto-load，传空 dict。"""
        from fastapi import FastAPI
        from gateway.control_plane import app as app_mod
        from agent.control_plane import config_adapter

        called = {"n": 0}
        monkeypatch.setattr(
            config_adapter,
            "load_runtime_configs",
            lambda: called.__setitem__("n", called["n"] + 1) or {"x": {}},
        )

        seen = {}
        original = app_mod.create_control_plane_app

        def spy(**kwargs):
            seen.update(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(app_mod, "create_control_plane_app", spy)

        main = FastAPI()
        mount_to(main, db_path=db_path, runtime_configs={})
        assert called["n"] == 0
        assert seen["runtime_configs"] == {}
