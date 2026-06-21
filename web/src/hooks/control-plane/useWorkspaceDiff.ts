/**
 * useWorkspaceDiff — 拉 GET /control-plane/workspaces/{id}/diff（摘要），
 * 暴露 loadUnified(path) 按需拉单文件的 unified diff（V1.1 P1 Diff 面板 Wave A）。
 *
 * 与基于 file.changed 事件的 FileChangePanel 互补：
 *   - 事件流是 runtime 写入时的「这一次 turn 改了什么」（实时）。
 *   - 这个 hook 是 git 当前实状态（HEAD vs 工作区），即使 turn 已经结束、
 *     页面刷新后也能看到累积改动。
 *
 * 行为：
 *   - 没 workspaceId → 不发请求，loading=false, files=[]
 *   - 有 workspaceId → 立刻 probe 一次，每 30s 轮询；reload() 手动触发
 *   - loadUnified(path) 第一次 lazy 拉取 + 缓存到 unifiedDiffs map；
 *     再次调用直接返回缓存
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchJSON } from "@/lib/api";

export type DiffFileEntry = {
  path: string;
  /** "create" | "edit" | "delete" — 与 file.changed 一致 */
  status: string;
  additions: number;
  deletions: number;
};

type DiffSummaryResponse = {
  workspace_id: string;
  files: DiffFileEntry[];
  total_additions: number;
  total_deletions: number;
  total_files: number;
};

type UnifiedDiffResponse = {
  workspace_id: string;
  diffs: Record<string, string>;
};

export type UseWorkspaceDiffResult = {
  files: DiffFileEntry[];
  totalAdditions: number;
  totalDeletions: number;
  totalFiles: number;
  loading: boolean;
  error: string | null;
  /** 单文件 unified diff 缓存。key = path */
  unifiedDiffs: Record<string, string>;
  /** 按需拉某个 path 的 unified diff，已缓存则直接返回。 */
  loadUnified: (path: string) => Promise<string>;
  /** 手动刷新摘要。 */
  reload: () => void;
};

const POLL_INTERVAL_MS = 30_000;

export function useWorkspaceDiff(
  workspaceId: string | null | undefined,
  base: string = "HEAD",
): UseWorkspaceDiffResult {
  const [files, setFiles] = useState<DiffFileEntry[]>([]);
  const [totalAdditions, setTotalAdditions] = useState(0);
  const [totalDeletions, setTotalDeletions] = useState(0);
  const [totalFiles, setTotalFiles] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unifiedDiffs, setUnifiedDiffs] = useState<Record<string, string>>({});

  const cancelledRef = useRef(false);
  const tickRef = useRef(0);

  const fetchSummary = useCallback(async (id: string, baseRef: string) => {
    const myTick = ++tickRef.current;
    try {
      const qs = new URLSearchParams();
      if (baseRef && baseRef !== "HEAD") qs.set("base", baseRef);
      const url = `/control-plane/workspaces/${encodeURIComponent(id)}/diff${
        qs.toString() ? "?" + qs : ""
      }`;
      const r = await fetchJSON<DiffSummaryResponse>(url);
      if (cancelledRef.current || myTick !== tickRef.current) return;
      setFiles(r.files || []);
      setTotalAdditions(r.total_additions ?? 0);
      setTotalDeletions(r.total_deletions ?? 0);
      setTotalFiles(r.total_files ?? (r.files?.length ?? 0));
      setError(null);
    } catch (e) {
      if (cancelledRef.current || myTick !== tickRef.current) return;
      setError((e as Error).message);
    } finally {
      if (!cancelledRef.current && myTick === tickRef.current) {
        setLoading(false);
      }
    }
  }, []);

  // workspaceId / base 切换时清缓存 + 重启轮询
  useEffect(() => {
    cancelledRef.current = false;
    // workspaceId 切换 → 清掉旧 workspace 的 unified diff 缓存。
    // 这是 effect 的合法副作用（外部状态切换时同步本地缓存），
    // react-hooks/set-state-in-effect 在这里是误报。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setUnifiedDiffs({});
    if (!workspaceId) {
      setFiles([]);
      setTotalAdditions(0);
      setTotalDeletions(0);
      setTotalFiles(0);
      setLoading(false);
      setError(null);
      return () => {
        cancelledRef.current = true;
      };
    }
    setLoading(true);
    void fetchSummary(workspaceId, base);
    const id = setInterval(() => {
      if (!cancelledRef.current) void fetchSummary(workspaceId, base);
    }, POLL_INTERVAL_MS);
    return () => {
      cancelledRef.current = true;
      clearInterval(id);
    };
  }, [workspaceId, base, fetchSummary]);

  const loadUnified = useCallback(
    async (path: string): Promise<string> => {
      if (!workspaceId) return "";
      // 缓存 hit
      const cached = unifiedDiffs[path];
      if (cached !== undefined) return cached;
      const qs = new URLSearchParams();
      qs.set("paths", path);
      if (base && base !== "HEAD") qs.set("base", base);
      const r = await fetchJSON<UnifiedDiffResponse>(
        `/control-plane/workspaces/${encodeURIComponent(workspaceId)}/diff/unified?${qs}`,
      );
      const diff = (r.diffs && r.diffs[path]) || "";
      // 写入缓存（即使空 diff 也写，避免反复 probe 空文件）
      setUnifiedDiffs((prev) => ({ ...prev, [path]: diff }));
      return diff;
    },
    [workspaceId, base, unifiedDiffs],
  );

  const reload = useCallback(() => {
    if (workspaceId) void fetchSummary(workspaceId, base);
  }, [workspaceId, base, fetchSummary]);

  return {
    files,
    totalAdditions,
    totalDeletions,
    totalFiles,
    loading,
    error,
    unifiedDiffs,
    loadUnified,
    reload,
  };
}
