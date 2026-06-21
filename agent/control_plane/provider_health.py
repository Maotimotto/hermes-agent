"""Provider 健康监控（V1.1 错误恢复 Wave C）。

职责：
1. 后台周期任务，每 ``poll_interval_sec`` 秒对 RuntimeRegistry 里所有
   runtime 调一次 ``health_check()``，把结果（available / message /
   version / latency_ms / 探活耗时）缓存到内存。
2. 状态变化时（available True↔False）追加一条 ``ProviderStatusChange``
   记录，环形 buffer 保留最近 N 条。
3. 提供同步 API ``snapshot(kind)`` / ``snapshot_all()`` 给路由层读缓存
   — 让 GET /providers 不再每次都阻塞在子进程探活上（claude SDK 的
   ``_import_sdk()`` + codex 的 ``check_codex_binary()`` 在 cold path
   都要 200ms+，频繁请求会抖）。
4. 新增 GET /providers/{kind}/history 接口给前端 ErrorBanner 用。

设计取舍：
  - 不写盘：状态历史只在内存（重启丢失），符合「下游有自然恢复机制就静默放行」
    的设计偏好（重启后下一次轮询自动重建）。
  - 不发 EventBus 全局事件：control-plane 的 EventBus 按 session_id 分区，
    没有「全局总线」概念。强行造一个 ``__provider__`` session 会污染
    /sessions/{id}/events 历史。前端通过 polling /providers 拿状态。
  - poll_interval_sec=30 默认值：与 web 前端 useProviders 的 15s 轮询周期
    错开，避免「前端拉到的就是最旧的缓存」。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅类型注解
    from gateway.control_plane.deps import RuntimeRegistry

logger = logging.getLogger(__name__)


# ── 数据结构 ────────────────────────────────────────────────────────


@dataclass
class ProviderHealthSnapshot:
    """单个 provider 的最新健康快照。"""

    kind: str
    available: bool
    message: str = ""
    version: str | None = None
    latency_ms: int | None = None        # provider 自报的延迟
    probe_duration_ms: int = 0           # 我们这次探活耗时
    checked_at: str = ""                 # ISO timestamp（探活完成时）
    error: str | None = None             # 探活时 health_check 抛了异常 → 文本

    def to_provider_info(self, *, active_sessions: int) -> dict[str, Any]:
        """转成 ProviderInfo schema 字段（路由层装成 ProviderInfo 模型）。"""
        return {
            "kind": self.kind,
            "available": self.available,
            "message": self.message,
            "version": self.version,
            "latency_ms": self.latency_ms,
            "active_sessions": active_sessions,
        }


@dataclass
class ProviderStatusChange:
    """状态变化记录（环形 buffer 项）。"""

    kind: str
    from_available: bool | None          # None = 第一次探活前没有旧状态
    to_available: bool
    message: str = ""
    at: str = ""                          # ISO timestamp


# ── ProviderHealthMonitor ──────────────────────────────────────────


class ProviderHealthMonitor:
    """周期性 provider 健康轮询 + 状态变化历史。"""

    def __init__(
        self,
        registry: "RuntimeRegistry",
        *,
        poll_interval_sec: float = 30.0,
        history_size: int = 50,
    ) -> None:
        self._registry = registry
        self._poll_interval = poll_interval_sec
        self._snapshots: dict[str, ProviderHealthSnapshot] = {}
        self._history: deque[ProviderStatusChange] = deque(maxlen=history_size)
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    # ── 生命周期 ───────────────────────────────────────────────

    async def start(self) -> None:
        """启动后台轮询任务。已启动则忽略。"""
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        # 立刻探一次，给 /providers 一个非空缓存
        await self.probe_all()
        self._task = asyncio.create_task(self._run(), name="provider-health-monitor")

    async def stop(self) -> None:
        """优雅停止。"""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        self._stop_event = None

    # ── 公开查询 ───────────────────────────────────────────────

    def snapshot(self, kind: str) -> ProviderHealthSnapshot | None:
        return self._snapshots.get(kind)

    def snapshot_all(self) -> dict[str, ProviderHealthSnapshot]:
        # 浅拷贝即可 — 调用方不应修改返回字典
        return dict(self._snapshots)

    def history(self, kind: str | None = None) -> list[ProviderStatusChange]:
        """返回状态变化历史。kind=None 返回全部，否则只返回该 kind 的。"""
        if kind is None:
            return list(self._history)
        return [h for h in self._history if h.kind == kind]

    # ── 探活 ───────────────────────────────────────────────────

    async def probe_one(self, kind: str) -> ProviderHealthSnapshot:
        """对单个 runtime 做一次 health_check 并更新缓存。"""
        runtime = self._registry.get(kind)
        if runtime is None:
            # 没注册的 kind 直接清快照（防止滑出 registry 后还残留旧数据）
            self._snapshots.pop(kind, None)
            raise KeyError(f"provider not registered: {kind!r}")

        started = time.perf_counter()
        snapshot: ProviderHealthSnapshot
        try:
            status = await runtime.health_check()
            duration_ms = int((time.perf_counter() - started) * 1000)
            snapshot = ProviderHealthSnapshot(
                kind=kind,
                available=bool(getattr(status, "available", False)),
                message=str(getattr(status, "message", "") or ""),
                version=getattr(status, "version", None),
                latency_ms=getattr(status, "latency", None),
                probe_duration_ms=duration_ms,
                checked_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:  # noqa: BLE001 — 兜底就是要这里
            duration_ms = int((time.perf_counter() - started) * 1000)
            snapshot = ProviderHealthSnapshot(
                kind=kind,
                available=False,
                message=f"health_check raised: {exc!s}",
                probe_duration_ms=duration_ms,
                checked_at=datetime.now(timezone.utc).isoformat(),
                error=str(exc)[:500],
            )
            logger.warning(
                "[health-monitor] %s health_check raised: %s",
                kind,
                exc,
            )

        self._record_transition(kind, snapshot)
        self._snapshots[kind] = snapshot
        return snapshot

    async def probe_all(self) -> dict[str, ProviderHealthSnapshot]:
        """对所有已注册 runtime 做一轮 health_check。"""
        kinds = sorted(self._registry._runtimes.keys())
        results: dict[str, ProviderHealthSnapshot] = {}
        for kind in kinds:
            try:
                results[kind] = await self.probe_one(kind)
            except KeyError:
                # 上面的 probe_one 在 kind 已不存在时抛 KeyError；忽略
                continue
            except Exception as exc:  # pragma: no cover — probe_one 已自包异常
                logger.error("[health-monitor] probe_all unexpected: %s", exc)
        return results

    # ── 内部 ───────────────────────────────────────────────────

    def _record_transition(
        self, kind: str, new_snapshot: ProviderHealthSnapshot
    ) -> None:
        prev = self._snapshots.get(kind)
        prev_avail = prev.available if prev is not None else None
        if prev_avail == new_snapshot.available:
            return
        self._history.append(
            ProviderStatusChange(
                kind=kind,
                from_available=prev_avail,
                to_available=new_snapshot.available,
                message=new_snapshot.message,
                at=new_snapshot.checked_at,
            )
        )
        if prev_avail is not None:
            # 不是首次 — 真的状态翻转，写日志方便排障
            logger.info(
                "[health-monitor] %s availability %s -> %s: %s",
                kind,
                prev_avail,
                new_snapshot.available,
                new_snapshot.message,
            )

    async def _run(self) -> None:
        """后台轮询 loop。"""
        assert self._stop_event is not None
        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._poll_interval,
                    )
                    # 收到 stop 信号 — 退出
                    return
                except asyncio.TimeoutError:
                    # 正常的轮询节拍
                    pass
                try:
                    await self.probe_all()
                except Exception as exc:  # pragma: no cover
                    logger.exception("[health-monitor] probe_all crashed: %s", exc)
        except asyncio.CancelledError:
            return
