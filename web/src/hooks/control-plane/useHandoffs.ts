/**
 * useHandoffs — Provider 转交 hook（V1.1 P1）
 *
 * GET /control-plane/sessions/:id/handoffs
 * POST /control-plane/sessions/:id/handoff
 */
import { useCallback, useEffect, useState } from "react";

export interface HandoffRecord {
  id: string;
  created_at: string;
  from_session_id: string;
  to_session_id: string;
  from_provider: string;
  to_provider: string;
  kind: "transfer" | "review";
  context_summary: string;
  initial_prompt: string;
  status: "ok" | "failed";
  error: string | null;
}

export interface CreateHandoffInput {
  target_provider: "claude" | "codex";
  kind?: "transfer" | "review";
  include_diff?: boolean;
  extra_prompt?: string;
  context_messages?: number;
}

export interface CreateHandoffResponse {
  handoff_id: string;
  to_session_id: string;
  from_provider: string;
  to_provider: string;
  kind: string;
  status: string;
}

export interface UseHandoffsReturn {
  handoffs: HandoffRecord[];
  loading: boolean;
  error: string | null;
  createHandoff: (input: CreateHandoffInput) => Promise<CreateHandoffResponse | null>;
  refresh: () => void;
}

export function useHandoffs(sessionId: string | null | undefined): UseHandoffsReturn {
  const [handoffs, setHandoffs] = useState<HandoffRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!sessionId) {
      /* eslint-disable-next-line react-hooks/set-state-in-effect -- 切 session 时清空 */
      setHandoffs([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`/control-plane/sessions/${encodeURIComponent(sessionId)}/handoffs`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) setHandoffs(data.handoffs ?? []);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, reloadKey]);

  const refresh = useCallback(() => {
    setReloadKey((k) => k + 1);
  }, []);

  const createHandoff = useCallback(
    async (input: CreateHandoffInput): Promise<CreateHandoffResponse | null> => {
      if (!sessionId) {
        setError("No session selected");
        return null;
      }
      try {
        const r = await fetch(
          `/control-plane/sessions/${encodeURIComponent(sessionId)}/handoff`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(input),
          }
        );
        if (!r.ok) {
          const body = await r.json().catch(() => ({}));
          throw new Error(body.message || body.detail?.message || `HTTP ${r.status}`);
        }
        const data: CreateHandoffResponse = await r.json();
        // 重拉列表
        setReloadKey((k) => k + 1);
        return data;
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
        return null;
      }
    },
    [sessionId]
  );

  return { handoffs, loading, error, createHandoff, refresh };
}
