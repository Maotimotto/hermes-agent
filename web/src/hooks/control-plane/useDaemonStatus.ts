/**
 * useDaemonStatus — daemon /health 状态轮询。
 *
 * 每 10s 拉一次 /control-plane/health，把整体 status 暴露成
 *   - "ok"        → 全绿
 *   - "degraded"  → 黄色（store 报错也算这一档）
 *   - "down"      → 网络炸了 / 5xx / fetch reject
 *
 * 同时暴露最近一次 raw HealthResponse，便于 TopBar 弹出 tooltip 显示
 * store / workspace / runtime 子系统细节。
 */

import { useEffect, useRef, useState } from "react";
import { fetchJSON } from "@/lib/api";

export type DaemonOverall = "ok" | "degraded" | "down";

export type DaemonHealth = {
  status: string;
  store?: { status?: string; detail?: string; session_count?: number };
  workspace?: { status?: string; detail?: string; count?: number };
  runtime?: { status?: string; registered?: string[]; active_turns?: number };
};

export type UseDaemonStatusResult = {
  overall: DaemonOverall;
  health: DaemonHealth | null;
  error: string | null;
  /** ms timestamp of last successful probe — for "Last seen Xs ago" UI. */
  lastOkAt: number | null;
};

const POLL_INTERVAL_MS = 10_000;

function classify(h: DaemonHealth): DaemonOverall {
  const s = (h.status || "").toLowerCase();
  if (s === "ok") return "ok";
  if (s === "degraded") return "degraded";
  return "down";
}

export function useDaemonStatus(): UseDaemonStatusResult {
  const [health, setHealth] = useState<DaemonHealth | null>(null);
  const [overall, setOverall] = useState<DaemonOverall>("down");
  const [error, setError] = useState<string | null>(null);
  const [lastOkAt, setLastOkAt] = useState<number | null>(null);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;

    const probe = async () => {
      try {
        const h = await fetchJSON<DaemonHealth>("/control-plane/health");
        if (cancelledRef.current) return;
        setHealth(h);
        const klass = classify(h);
        setOverall(klass);
        setError(null);
        if (klass === "ok") setLastOkAt(Date.now());
      } catch (e) {
        if (cancelledRef.current) return;
        setOverall("down");
        setError((e as Error).message);
      }
    };

    void probe();
    const id = setInterval(probe, POLL_INTERVAL_MS);
    return () => {
      cancelledRef.current = true;
      clearInterval(id);
    };
  }, []);

  return { overall, health, error, lastOkAt };
}
