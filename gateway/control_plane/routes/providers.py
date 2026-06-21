"""Provider API routes for the Daemon API control plane.

Endpoints:
  GET /providers                    — 列出所有已注册 runtime 的健康状态
  GET /providers/{kind}             — 单个 provider 的健康状态
  GET /providers/{kind}/history     — provider 状态变化历史（V1.1 错误恢复）

实现说明：
  Daemon 启动时通过 ``build_default_runtimes`` 把 provider 注册进
  ``RuntimeRegistry``，并启动 ``ProviderHealthMonitor`` 后台轮询。
  本路由优先读 monitor 缓存，cache miss 才走同步 health_check 兜底。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from agent.control_plane.provider_health import (
    ProviderHealthMonitor,
    ProviderHealthSnapshot,
)
from gateway.control_plane.deps import (
    RuntimeRegistry,
    get_provider_health,
    get_runtime_registry,
)
from gateway.control_plane.schemas import (
    ProviderInfo,
    ProviderListResponse,
    ProviderStatusChangeItem,
    ProviderStatusHistoryResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/providers", tags=["providers"])


def _count_active_sessions(registry: RuntimeRegistry, kind: str) -> int:
    """统计绑定到指定 runtime kind 的 session 数。"""
    return sum(1 for v in registry._session_runtime.values() if v == kind)


def _snapshot_to_provider_info(
    snapshot: ProviderHealthSnapshot, *, active_sessions: int
) -> ProviderInfo:
    return ProviderInfo(
        kind=snapshot.kind,
        available=snapshot.available,
        message=snapshot.message,
        version=snapshot.version,
        latency_ms=snapshot.latency_ms,
        active_sessions=active_sessions,
    )


async def _probe_fallback(
    kind: str, runtime, active_sessions: int
) -> ProviderInfo:
    """monitor 未注入时的兜底 — 直接调 health_check。"""
    try:
        status = await runtime.health_check()
        return ProviderInfo(
            kind=kind,
            available=bool(status.available),
            message=status.message or "",
            version=getattr(status, "version", None),
            latency_ms=getattr(status, "latency", None),
            active_sessions=active_sessions,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[providers] health_check fallback failed kind=%s", kind)
        return ProviderInfo(
            kind=kind,
            available=False,
            message=f"health_check raised: {exc!s}",
            version=None,
            latency_ms=None,
            active_sessions=active_sessions,
        )


@router.get("", response_model=ProviderListResponse)
async def list_providers(
    registry: RuntimeRegistry = Depends(get_runtime_registry),
    monitor: ProviderHealthMonitor | None = Depends(get_provider_health),
) -> ProviderListResponse:
    """列出所有已注册的 provider 及其健康状态。

    优先读 monitor 缓存；缓存为空（启动初期）才走同步 health_check。
    """
    kinds = sorted(registry._runtimes.keys())
    items: list[ProviderInfo] = []
    snapshots = monitor.snapshot_all() if monitor is not None else {}
    for kind in kinds:
        active = _count_active_sessions(registry, kind)
        snap = snapshots.get(kind)
        if snap is not None:
            items.append(_snapshot_to_provider_info(snap, active_sessions=active))
        else:
            runtime = registry._runtimes[kind]
            items.append(await _probe_fallback(kind, runtime, active))
    return ProviderListResponse(providers=items)


@router.get("/{kind}", response_model=ProviderInfo)
async def get_provider(
    kind: str,
    registry: RuntimeRegistry = Depends(get_runtime_registry),
    monitor: ProviderHealthMonitor | None = Depends(get_provider_health),
) -> ProviderInfo:
    """单个 provider 的健康状态。kind 不存在返回 404。"""
    runtime = registry.get(kind)
    if runtime is None:
        raise HTTPException(
            status_code=404,
            detail=f"provider not registered: {kind!r}",
        )
    active = _count_active_sessions(registry, kind)
    if monitor is not None:
        snap = monitor.snapshot(kind)
        if snap is not None:
            return _snapshot_to_provider_info(snap, active_sessions=active)
    return await _probe_fallback(kind, runtime, active)


@router.get(
    "/{kind}/history",
    response_model=ProviderStatusHistoryResponse,
)
async def get_provider_history(
    kind: str,
    registry: RuntimeRegistry = Depends(get_runtime_registry),
    monitor: ProviderHealthMonitor | None = Depends(get_provider_health),
) -> ProviderStatusHistoryResponse:
    """provider 健康状态变化历史。最早 → 最新顺序。

    监控未启动时返回空列表（不报错）— 让前端在 daemon 早期阶段也能稳定渲染。
    """
    if registry.get(kind) is None:
        raise HTTPException(
            status_code=404,
            detail=f"provider not registered: {kind!r}",
        )
    if monitor is None:
        return ProviderStatusHistoryResponse(kind=kind, history=[])
    items = [
        ProviderStatusChangeItem(
            kind=h.kind,
            from_available=h.from_available,
            to_available=h.to_available,
            message=h.message,
            at=h.at,
        )
        for h in monitor.history(kind)
    ]
    return ProviderStatusHistoryResponse(kind=kind, history=items)
