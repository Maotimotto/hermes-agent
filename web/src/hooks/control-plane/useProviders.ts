/**
 * useProviders — 拉取 /control-plane/providers，每 15s 刷新。
 *
 * 暴露：
 *   - providers: 每个 provider 的可用性 + active_sessions
 *   - error: 最近一次拉取错误（不影响上一次成功的数据）
 */

import { useEffect, useRef, useState } from "react";
import { fetchJSON } from "@/lib/api";

export type ProviderInfo = {
  kind: string;
  available: boolean;
  message?: string;
  version?: string | null;
  latency_ms?: number | null;
  active_sessions?: number;
};

type ProviderListResponse = {
  providers: ProviderInfo[];
};

export type UseProvidersResult = {
  providers: ProviderInfo[];
  error: string | null;
  loading: boolean;
};

const POLL_INTERVAL_MS = 15_000;

export function useProviders(): UseProvidersResult {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;

    const probe = async () => {
      try {
        const r = await fetchJSON<ProviderListResponse>(
          "/control-plane/providers",
        );
        if (cancelledRef.current) return;
        setProviders(r.providers || []);
        setError(null);
      } catch (e) {
        if (cancelledRef.current) return;
        setError((e as Error).message);
      } finally {
        if (!cancelledRef.current) setLoading(false);
      }
    };

    void probe();
    const id = setInterval(probe, POLL_INTERVAL_MS);
    return () => {
      cancelledRef.current = true;
      clearInterval(id);
    };
  }, []);

  return { providers, error, loading };
}
