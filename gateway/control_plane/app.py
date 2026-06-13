"""FastAPI sub-application for the Daemon API control plane.

Usage::

    from gateway.control_plane import mount_to

    # In your existing FastAPI main app:
    app = FastAPI(...)
    mount_to(app)

This registers all control-plane routes under ``/control-plane`` and sets
up the required singletons (SessionStore, EventBus, RuntimeRegistry,
WorkspaceManager).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

from gateway.control_plane.deps import AppState
from gateway.control_plane.routes import approvals, events, health, sessions, turns

logger = logging.getLogger(__name__)


def create_control_plane_app(
    *,
    db_path: str | None = None,
    run_migrations: bool = True,
    runtime_configs: dict[str, dict[str, Any]] | None = None,
) -> FastAPI:
    """Build the FastAPI sub-app for the control plane.

    Parameters
    ----------
    db_path : str | None
        Path to the SQLite database.  Defaults to ``~/.hermes/control_plane.db``.
    run_migrations : bool
        Whether to run DB migrations on startup (default True).
    runtime_configs : dict[str, dict] | None
        Mapping kind → provider config used by :func:`build_default_runtimes`.
        When ``None`` (default) no runtime is registered automatically;
        the caller is expected to ``state.runtime_registry.register(...)``
        manually (e.g. tests with mocks).
    """
    app = FastAPI(
        title="Hermes Daemon API",
        version="1.0.0",
    )

    # Attach state container
    state = AppState()
    app.state.cp = state  # type: ignore[attr-defined]

    # Store config for deferred init
    app.state._cp_db_path = db_path  # type: ignore[attr-defined]
    app.state._cp_runtime_configs = runtime_configs  # type: ignore[attr-defined]
    app.state._cp_initialized = False  # type: ignore[attr-defined]

    # Register startup event to init store
    @app.on_event("startup")
    async def _startup() -> None:
        await init_store(app)

    # Register shutdown event to close store
    @app.on_event("shutdown")
    async def _shutdown() -> None:
        st: AppState = app.state.cp  # type: ignore[assignment]
        if st.store is not None:
            await st.store.close()
            logger.info("[control-plane] store closed")

    # Mount route modules (all paths are relative to /control-plane)
    app.include_router(sessions.router)
    app.include_router(turns.router)
    app.include_router(events.router)
    app.include_router(approvals.router)
    app.include_router(health.router)

    return app


async def init_store(app: FastAPI) -> None:
    """Initialize the SessionStore on the given app.

    Idempotent — safe to call multiple times.
    """
    if app.state._cp_initialized:  # type: ignore[attr-defined]
        return

    state: AppState = app.state.cp  # type: ignore[assignment]
    db_path = app.state._cp_db_path  # type: ignore[attr-defined]
    runtime_configs = app.state._cp_runtime_configs  # type: ignore[attr-defined]

    from agent.control_plane.store import SessionStore

    store = SessionStore(db_path=db_path)
    await store.init()
    state.store = store
    # Wire the EventBus persistence hook to the store so every published
    # event is also written to the events table.
    state.event_bus._store = store  # type: ignore[attr-defined]
    # Eagerly create the ApprovalGate now that the store is ready.
    gate = state.ensure_approval_gate()

    # Wave 8.2: 用工厂构造默认 runtime 并注册到 RuntimeRegistry
    if runtime_configs:
        from agent.control_plane.runtimes.factory import build_default_runtimes

        runtimes = build_default_runtimes(
            store=store,
            approval_gate=gate,
            runtime_configs=runtime_configs,
        )
        for kind, rt in runtimes.items():
            state.runtime_registry.register(kind, rt)
        logger.info(
            "[control-plane] registered runtimes: %s",
            sorted(runtimes.keys()),
        )

    app.state._cp_initialized = True  # type: ignore[attr-defined]
    logger.info("[control-plane] store initialized (db_path=%s)", db_path or "default")


def mount_to(
    main_app: FastAPI,
    *,
    prefix: str = "/control-plane",
    db_path: str | None = None,
    runtime_configs: dict[str, dict[str, Any]] | None = None,
    auto_load_runtime_configs: bool = True,
) -> None:
    """Mount the control-plane sub-app onto an existing FastAPI application.

    Call this once from your gateway ``main.py``::

        from gateway.control_plane import mount_to
        mount_to(app)  # auto-loads runtime_configs from ~/.hermes/config.yaml

    Or override::

        mount_to(app, runtime_configs={
            "claude": {"api_key": "...", "model": "..."},
            "codex":  {"codex_bin": "codex"},
        })

    Parameters
    ----------
    main_app : FastAPI
        The parent FastAPI application.
    prefix : str
        URL prefix for all control-plane routes (default ``/control-plane``).
    db_path : str | None
        Override the SQLite DB path (mostly useful in tests).
    runtime_configs : dict[str, dict] | None
        Per-runtime config dict; passed through to ``build_default_runtimes``.
        When ``None`` and ``auto_load_runtime_configs=True``, auto-load from
        hermes config; pass ``runtime_configs={}`` to explicitly disable.
    auto_load_runtime_configs : bool
        When True (default) and ``runtime_configs is None``, call
        :func:`agent.control_plane.config_adapter.load_runtime_configs`.
    """
    if runtime_configs is None and auto_load_runtime_configs:
        try:
            from agent.control_plane.config_adapter import load_runtime_configs

            runtime_configs = load_runtime_configs()
            if runtime_configs:
                logger.info(
                    "[control-plane] auto-loaded runtime_configs: %s",
                    sorted(runtime_configs.keys()),
                )
        except Exception:
            logger.exception(
                "[control-plane] failed to auto-load runtime_configs from hermes config"
            )
            runtime_configs = None

    cp_app = create_control_plane_app(
        db_path=db_path, runtime_configs=runtime_configs
    )
    main_app.mount(prefix, cp_app)
    logger.info("[control-plane] mounted at %s", prefix)
