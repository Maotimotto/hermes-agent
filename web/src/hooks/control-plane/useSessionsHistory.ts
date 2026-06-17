/**
 * useSessionsHistory — 历史会话浏览（带 status 过滤 + 分页）。
 *
 * 与 useSessions 的区别：useSessions 默认只看活跃 session 列表 + 创建/选中 +
 * 实时刷新；useSessionsHistory 面向 HistoryPage，更倾向 read-only 翻页 +
 * 客户端关键字搜索（按 id / repo_path / model 模糊匹配）。
 *
 * 后端 GET /control-plane/sessions 已经支持 status / limit / offset，
 * 这里负责把它们装在 hook 内拉数据 + 暴露下一页 / 上一页 helper。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchJSON } from "@/lib/api";
import type {
  SessionRecord,
  SessionListResponse,
} from "@/pages/control-plane/types";

export type StatusFilter = "all" | "running" | "stopped" | "failed" | "completed" | "created" | "waiting" | "cancelled";

export type UseSessionsHistoryResult = {
  rows: SessionRecord[];
  filteredRows: SessionRecord[];
  loading: boolean;
  error: string | null;
  page: number;
  pageSize: number;
  /** Client-side query — never sent to backend; filters in-memory. */
  query: string;
  setQuery: (q: string) => void;
  status: StatusFilter;
  setStatus: (s: StatusFilter) => void;
  setPage: (n: number) => void;
  refresh: () => Promise<void>;
  /** Helper: optimistic-remove a row after a successful DELETE. */
  removeRow: (sid: string) => void;
};

const DEFAULT_PAGE_SIZE = 25;

export function useSessionsHistory(
  pageSize = DEFAULT_PAGE_SIZE,
): UseSessionsHistoryResult {
  const [rows, setRows] = useState<SessionRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      params.set("limit", String(pageSize));
      params.set("offset", String(page * pageSize));
      if (status !== "all") params.set("status", status);
      const r = await fetchJSON<SessionListResponse>(
        `/control-plane/sessions?${params.toString()}`,
      );
      setRows(r.sessions);
    } catch (e) {
      setError(`Failed to load sessions: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, status]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const filteredRows = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((s) => {
      const haystack = [
        s.id,
        s.runtime_kind,
        s.model,
        s.repo_path || "",
        s.status,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [rows, query]);

  const removeRow = useCallback((sid: string) => {
    setRows((prev) => prev.filter((s) => s.id !== sid));
  }, []);

  return {
    rows,
    filteredRows,
    loading,
    error,
    page,
    pageSize,
    query,
    setQuery,
    status,
    setStatus,
    setPage,
    refresh,
    removeRow,
  };
}
