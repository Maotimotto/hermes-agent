"""Provider API routes for the Daemon API control plane.

Endpoints:
  GET /providers           — 列出所有已注册 runtime 的健康状态
  GET /providers/{kind}    — 单个 provider 的详细健康状态

实现说明：
  Daemon 启动时通过 ``build_default_runtimes`` 把 provider 注册进
  ``RuntimeRegistry``。本路由把它们的 ``health_check()`` 结果暴露成 HTTP，
  让前端 / SDK 不必自己 import runtime 工厂就能问「现在哪个 provider 可用」。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from gateway.control_plane.deps import RuntimeRegistry, get_runtime_registry
from gateway.control_plane.schemas import ProviderInfo, ProviderListResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/providers", tags=["providers"])


async def _probe(kind: str, runtime, active_sessions: int) -> ProviderInfo:
    """对单个 runtime 执行 health_check 并打包成 ProviderInfo。

    异常不上抛 —— health_check 本身就是用来回答「我是不是挂了」的，
    一旦它自己抛了，我们就把它视为 unavailable 并把异常文本带回。
    """
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
    except Exception as exc:  # noqa: BLE001 — 这里就是要兜底
        logger.exception("[providers] health_check failed kind=%s", kind)
        return ProviderInfo(
            kind=kind,
            available=False,
            message=f"health_check raised: {exc!s}",
            version=None,
            latency_ms=None,
            active_sessions=active_sessions,
        )


def _count_active_sessions(registry: RuntimeRegistry, kind: str) -> int:
    """统计绑定到指定 runtime kind 的 session 数。"""
    return sum(1 for v in registry._session_runtime.values() if v == kind)


@router.get("", response_model=ProviderListResponse)
async def list_providers(
    registry: RuntimeRegistry = Depends(get_runtime_registry),
) -> ProviderListResponse:
    """列出所有已注册的 provider 及其健康状态。

    顺序按 kind 字典序，方便前端稳定渲染。
    """
    kinds = sorted(registry._runtimes.keys())
    items: list[ProviderInfo] = []
    for kind in kinds:
        runtime = registry._runtimes[kind]
        active = _count_active_sessions(registry, kind)
        items.append(await _probe(kind, runtime, active))
    return ProviderListResponse(providers=items)


@router.get("/{kind}", response_model=ProviderInfo)
async def get_provider(
    kind: str,
    registry: RuntimeRegistry = Depends(get_runtime_registry),
) -> ProviderInfo:
    """单个 provider 的健康状态。kind 不存在返回 404。"""
    runtime = registry.get(kind)
    if runtime is None:
        raise HTTPException(
            status_code=404,
            detail=f"provider not registered: {kind!r}",
        )
    active = _count_active_sessions(registry, kind)
    return await _probe(kind, runtime, active)
