/**
 * useSessions — control-plane sessions list state.
 *
 * Wraps GET /control-plane/sessions in a self-contained hook so the page only
 * needs to render. Auto-selects the first session when nothing is selected, and
 * exposes create/delete helpers that refresh + re-select correctly.
 */

import { useCallback, useEffect, useState } from "react";
import { fetchJSON } from "@/lib/api";
import type {
  SessionRecord,
  SessionListResponse,
} from "@/pages/control-plane/types";

export type CreateSessionInput = {
  runtime_kind: "claude" | "codex";
  model: string;
  repo_path?: string;
};

export type UseSessionsResult = {
  sessions: SessionRecord[];
  selectedSid: string | null;
  setSelectedSid: (sid: string | null) => void;
  query: string;
  setQuery: (q: string) => void;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  createSession: (input: CreateSessionInput) => Promise<SessionRecord | null>;
  deleteSession: (sid: string) => Promise<void>;
};

const DEBOUNCE_MS = 300;

function sessionsPath(query: string): string {
  const params = new URLSearchParams();
  params.set("limit", "50");
  const q = query.trim();
  if (q) params.set("q", q);
  return `/control-plane/sessions?${params.toString()}`;
}

export function useSessions(): UseSessionsResult {
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [selectedSid, setSelectedSid] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchJSON<SessionListResponse>(sessionsPath(query));
      setSessions(data.sessions);
      setSelectedSid((cur) => {
        if (cur) return cur;
        return data.sessions[0]?.id ?? null;
      });
    } catch (e) {
      setError(`Failed to load sessions: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [query]);

  const createSession = useCallback(
    async (input: CreateSessionInput) => {
      setError(null);
      try {
        const body: Record<string, unknown> = {
          runtime_kind: input.runtime_kind,
          model: input.model,
        };
        if (input.repo_path && input.repo_path.trim()) {
          body.repo_path = input.repo_path.trim();
        }
        const data = await fetchJSON<SessionRecord>("/control-plane/sessions", {
          method: "POST",
          body: JSON.stringify(body),
          headers: { "Content-Type": "application/json" },
        });
        await refresh();
        setSelectedSid(data.id);
        return data;
      } catch (e) {
        setError(`Create session failed: ${(e as Error).message}`);
        return null;
      }
    },
    [refresh],
  );

  const deleteSession = useCallback(
    async (sid: string) => {
      try {
        await fetchJSON(`/control-plane/sessions/${sid}`, { method: "DELETE" });
        setSelectedSid((cur) => (cur === sid ? null : cur));
        await refresh();
      } catch (e) {
        setError(`Delete failed: ${(e as Error).message}`);
      }
    },
    [refresh],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refresh();
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [query, refresh]);

  return {
    sessions,
    selectedSid,
    setSelectedSid,
    query,
    setQuery,
    loading,
    error,
    refresh,
    createSession,
    deleteSession,
  };
}
