"""Health check route for the Daemon API control plane.

Endpoint:
  GET /health — returns runtime/store/workspace status
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from agent.control_plane.store import SessionStore
from agent.control_plane.workspace import WorkspaceManager
from gateway.control_plane.deps import (
    AppState,
    RuntimeRegistry,
    get_app_state,
    get_runtime_registry,
    get_store,
    get_workspace_manager,
)
from gateway.control_plane.schemas import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(
    store: SessionStore = Depends(get_store),
    runtime_registry: RuntimeRegistry = Depends(get_runtime_registry),
    workspace_manager: WorkspaceManager = Depends(get_workspace_manager),
) -> HealthResponse:
    """Return health status for store, workspace, and runtime subsystems."""
    store_status: dict[str, Any] = {"status": "ok"}
    try:
        # Lightweight DB probe — driver-agnostic.
        row = await store.driver.fetchone("SELECT count(*) AS n FROM sessions")
        store_status["session_count"] = (row or {}).get("n", 0)
    except Exception as exc:
        store_status = {"status": "error", "detail": str(exc)}

    workspace_status: dict[str, Any] = {"status": "ok"}
    try:
        ws_list = await workspace_manager.list_workspaces()
        workspace_status["count"] = len(ws_list)
    except Exception as exc:
        workspace_status = {"status": "error", "detail": str(exc)}

    runtime_status: dict[str, Any] = {"status": "ok"}
    registered = list(runtime_registry._runtimes.keys())
    runtime_status["registered"] = registered
    runtime_status["active_turns"] = len(runtime_registry._active_turns)

    overall = "ok"
    if store_status.get("status") == "error":
        overall = "degraded"

    return HealthResponse(
        status=overall,
        store=store_status,
        workspace=workspace_status,
        runtime=runtime_status,
    )
