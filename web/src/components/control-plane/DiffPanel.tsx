/**
 * DiffPanel — workspace 实状态 diff 视图（V1.1 P1 Diff 面板 Wave A）。
 *
 * 与 FileChangePanel 区别：
 *   - FileChangePanel 消费 file.changed 事件流（运行时实时变更，turn 结束就没了）
 *   - DiffPanel 走 GET /workspaces/{id}/diff 拉 git 实状态（HEAD 对比工作区，
 *     turn 已经结束、刷新页面、跨 session 也能看到累积改动）
 *
 * 行为：
 *   - 顶部 header：file 计数 + 总 +/-；reload 按钮 + loading dot
 *   - 文件列表：A/M/D 标识徽章 + path + +/- 统计 + Diff 按钮
 *   - 点 Diff 按需 lazy 拉 unified diff，渲染带行级高亮的 <pre>
 *   - 单文件 diff 默认折叠 + 200 行截断（与 FileChangePanel 视觉一致）
 */

import { useMemo, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  useWorkspaceDiff,
  type DiffFileEntry,
} from "@/hooks/control-plane/useWorkspaceDiff";
import {
  DiffRenderer,
  DiffViewSwitcher,
  type DiffViewMode,
} from "./diff-renderer";

const ALL_STATUSES = ["create", "edit", "delete"] as const;
type DiffStatus = (typeof ALL_STATUSES)[number];

const STATUS_META: Record<string, { label: string; color: string; bg: string }> = {
  create: { label: "A", color: "#065f46", bg: "#d1fae5" },
  edit: { label: "M", color: "#92400e", bg: "#fef3c7" },
  delete: { label: "D", color: "#991b1b", bg: "#fee2e2" },
};

export type DiffPanelProps = {
  workspaceId: string | null | undefined;
  defaultOpen?: boolean;
};

export function DiffPanel({ workspaceId, defaultOpen = true }: DiffPanelProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [viewMode, setViewMode] = useState<DiffViewMode>("unified");
  // base ref：实际生效的（提交后才发请求）；baseInput：输入框 buffer
  const [base, setBase] = useState("HEAD");
  const [baseInput, setBaseInput] = useState("HEAD");
  const [statusFilter, setStatusFilter] = useState<Set<DiffStatus>>(
    () => new Set(ALL_STATUSES),
  );
  const [pathQuery, setPathQuery] = useState("");
  const {
    files,
    totalAdditions,
    totalDeletions,
    totalFiles,
    loading,
    error,
    unifiedDiffs,
    loadUnified,
    fetchUnifiedBatch,
    reload,
  } = useWorkspaceDiff(workspaceId, base);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const visibleFiles = useMemo(() => {
    const q = pathQuery.trim().toLowerCase();
    return files.filter((f) => {
      if (!statusFilter.has(f.status as DiffStatus)) return false;
      if (q && !f.path.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [files, statusFilter, pathQuery]);

  const visibleTotals = useMemo(() => {
    return visibleFiles.reduce(
      (acc, f) => ({
        adds: acc.adds + f.additions,
        dels: acc.dels + f.deletions,
      }),
      { adds: 0, dels: 0 },
    );
  }, [visibleFiles]);

  if (!workspaceId) return null;
  if (!loading && files.length === 0 && !error) return null;

  const filtered = visibleFiles.length !== files.length;

  const toggleStatus = (s: DiffStatus) => {
    setStatusFilter((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      // 全空时回填全开（避免误把列表过滤成空）
      if (next.size === 0) return new Set(ALL_STATUSES);
      return next;
    });
  };

  const submitBase = () => {
    const trimmed = baseInput.trim() || "HEAD";
    if (trimmed !== base) setBase(trimmed);
  };

  const exportPatch = async () => {
    if (visibleFiles.length === 0 || exporting) return;
    setExporting(true);
    setExportError(null);
    try {
      const paths = visibleFiles.map((f) => f.path);
      // 优先用 cache，缺的批量拉一次
      const missing = paths.filter((p) => unifiedDiffs[p] === undefined);
      let fetched: Record<string, string> = {};
      if (missing.length > 0) {
        fetched = await fetchUnifiedBatch(missing);
      }
      const parts: string[] = [];
      for (const p of paths) {
        const text = unifiedDiffs[p] ?? fetched[p] ?? "";
        if (text.trim()) parts.push(text.endsWith("\n") ? text : text + "\n");
      }
      const body = parts.join("");
      if (!body) {
        setExportError("nothing to export");
        return;
      }
      const blob = new Blob([body], { type: "text/x-patch" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const ts = new Date()
        .toISOString()
        .replace(/[:T]/g, "-")
        .replace(/\..+/, "");
      const safeBase = base.replace(/[^a-zA-Z0-9_.-]/g, "_");
      const safeWid = (workspaceId ?? "ws").slice(0, 12);
      a.href = url;
      a.download = `workspace-${safeWid}-${safeBase}-${ts}.patch`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      // 浏览器拿到 blob 后释放
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (e) {
      setExportError((e as Error).message);
    } finally {
      setExporting(false);
    }
  };

  return (
    <motion.section
      layout="position"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.18 }}
      style={{
        margin: "10px 0",
        border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 14%, transparent)",
        borderRadius: 8,
        background: "color-mix(in srgb, var(--midground-base, #ffe6cb) 3%, var(--background-base, #041c1c))",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 10px",
        }}
      >
        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          style={{
            background: "transparent",
            border: "none",
            cursor: "pointer",
            color: "var(--midground, #ffe6cb)",
            fontFamily: "var(--theme-font-mono, monospace)",
            fontSize: 12,
            fontWeight: 700,
            letterSpacing: 0.4,
            display: "flex",
            alignItems: "center",
            gap: 8,
            flex: 1,
            textAlign: "left",
          }}
        >
          <span aria-hidden style={{ color: "var(--color-text-secondary)" }}>
            {open ? "▾" : "▸"}
          </span>
          <span>WORKSPACE DIFF</span>
          {loading && (
            <motion.span
              aria-label="loading"
              animate={{ opacity: [0.3, 1, 0.3] }}
              transition={{ duration: 1.2, repeat: Infinity, ease: "easeInOut" }}
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: "#9ca3af",
                marginLeft: 2,
              }}
            />
          )}
          <span style={{ color: "var(--color-text-secondary, #6b7280)", fontWeight: 400 }}>
            {filtered
              ? `${visibleFiles.length}/${totalFiles}`
              : `${totalFiles}`}{" "}
            file{totalFiles === 1 ? "" : "s"}
          </span>
          <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8 }}>
            <span style={{ color: "#34d399" }}>
              +{filtered ? visibleTotals.adds : totalAdditions}
            </span>
            <span style={{ color: "#fb7185" }}>
              −{filtered ? visibleTotals.dels : totalDeletions}
            </span>
          </span>
        </button>
        <DiffViewSwitcher mode={viewMode} onChange={setViewMode} />
        <button
          onClick={(e) => {
            e.stopPropagation();
            void exportPatch();
          }}
          disabled={exporting || visibleFiles.length === 0}
          title="Export visible files as a single .patch"
          style={{
            padding: "2px 8px",
            fontSize: 11,
            border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 18%, transparent)",
            borderRadius: 3,
            background: "transparent",
            color:
              exporting || visibleFiles.length === 0
                ? "var(--color-text-secondary, #6b7280)"
                : "var(--midground, #ffe6cb)",
            cursor: exporting ? "wait" : visibleFiles.length === 0 ? "not-allowed" : "pointer",
            fontFamily: "var(--theme-font-mono, monospace)",
          }}
        >
          {exporting ? "…" : "↓ .patch"}
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            reload();
          }}
          title="Reload diff"
          style={{
            padding: "2px 8px",
            fontSize: 11,
            border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 18%, transparent)",
            borderRadius: 3,
            background: "transparent",
            color: "var(--color-text-secondary, #6b7280)",
            cursor: "pointer",
            fontFamily: "var(--theme-font-mono, monospace)",
          }}
        >
          ↻
        </button>
      </div>

      {/* toolbar 第二行：base ref / status filter / path search (Wave C) */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "0 10px 8px 10px",
          fontFamily: "var(--theme-font-mono, monospace)",
          fontSize: 11,
          color: "var(--color-text-secondary, #6b7280)",
        }}
      >
        <label style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          base
          <input
            type="text"
            value={baseInput}
            onChange={(e) => setBaseInput(e.target.value)}
            onBlur={submitBase}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submitBase();
              }
            }}
            placeholder="HEAD"
            spellCheck={false}
            style={{
              width: 90,
              padding: "1px 6px",
              fontSize: 11,
              fontFamily: "inherit",
              border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 18%, transparent)",
              borderRadius: 3,
              background: "transparent",
              color: "var(--midground, #ffe6cb)",
              outline: "none",
            }}
          />
        </label>

        <div style={{ display: "inline-flex", gap: 4 }}>
          {ALL_STATUSES.map((s) => {
            const meta = STATUS_META[s];
            const active = statusFilter.has(s);
            return (
              <button
                key={s}
                type="button"
                onClick={() => toggleStatus(s)}
                title={s}
                style={{
                  padding: "1px 6px",
                  fontSize: 10,
                  border: `1px solid ${
                    active
                      ? meta.color
                      : "color-mix(in srgb, var(--midground-base, #ffe6cb) 14%, transparent)"
                  }`,
                  borderRadius: 3,
                  background: active ? meta.bg : "transparent",
                  color: active ? meta.color : "var(--color-text-secondary, #6b7280)",
                  fontWeight: active ? 700 : 400,
                  cursor: "pointer",
                  fontFamily: "inherit",
                }}
              >
                {meta.label}
              </button>
            );
          })}
        </div>

        <input
          type="text"
          value={pathQuery}
          onChange={(e) => setPathQuery(e.target.value)}
          placeholder="filter path…"
          spellCheck={false}
          style={{
            flex: 1,
            padding: "1px 6px",
            fontSize: 11,
            fontFamily: "inherit",
            border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 18%, transparent)",
            borderRadius: 3,
            background: "transparent",
            color: "var(--midground, #ffe6cb)",
            outline: "none",
          }}
        />
        {(pathQuery ||
          statusFilter.size !== ALL_STATUSES.length ||
          base !== "HEAD") && (
          <button
            type="button"
            onClick={() => {
              setPathQuery("");
              setStatusFilter(new Set(ALL_STATUSES));
              setBaseInput("HEAD");
              if (base !== "HEAD") setBase("HEAD");
            }}
            title="Clear filters"
            style={{
              padding: "1px 6px",
              fontSize: 10,
              border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 18%, transparent)",
              borderRadius: 3,
              background: "transparent",
              color: "var(--color-text-secondary, #6b7280)",
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            clear
          </button>
        )}
      </div>

      {error && (
        <div
          style={{
            padding: "6px 12px",
            fontSize: 11,
            color: "#fca5a5",
            background: "color-mix(in srgb, #ef4444 8%, transparent)",
            borderTop: "1px solid color-mix(in srgb, #ef4444 22%, transparent)",
          }}
        >
          diff error: {error}
        </div>
      )}

      {exportError && (
        <div
          style={{
            padding: "6px 12px",
            fontSize: 11,
            color: "#fca5a5",
            background: "color-mix(in srgb, #ef4444 8%, transparent)",
            borderTop: "1px solid color-mix(in srgb, #ef4444 22%, transparent)",
          }}
        >
          export error: {exportError}
        </div>
      )}

      <AnimatePresence initial={false}>
        {open && visibleFiles.length > 0 && (
          <motion.ul
            key="list"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            style={{
              listStyle: "none",
              margin: 0,
              padding: 0,
              borderTop: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 10%, transparent)",
              overflow: "hidden",
            }}
          >
            {visibleFiles.map((f) => (
              <DiffFileRow
                key={f.path}
                file={f}
                cachedDiff={unifiedDiffs[f.path]}
                onLoad={() => loadUnified(f.path)}
                viewMode={viewMode}
              />
            ))}
          </motion.ul>
        )}
      </AnimatePresence>

      {open && files.length > 0 && visibleFiles.length === 0 && (
        <div
          style={{
            padding: "10px 12px",
            fontSize: 11,
            color: "var(--color-text-secondary, #6b7280)",
            borderTop: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 10%, transparent)",
            fontStyle: "italic",
          }}
        >
          no files match current filter
        </div>
      )}
    </motion.section>
  );
}

// ── Row ────────────────────────────────────────────────────────────────

function DiffFileRow({
  file,
  cachedDiff,
  onLoad,
  viewMode,
}: {
  file: DiffFileEntry;
  cachedDiff: string | undefined;
  onLoad: () => Promise<string>;
  viewMode: DiffViewMode;
}) {
  const [open, setOpen] = useState(false);
  const [diffText, setDiffText] = useState<string | undefined>(cachedDiff);
  const [loadingDiff, setLoadingDiff] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const meta = STATUS_META[file.status] ?? STATUS_META.edit;

  const toggle = async () => {
    if (!open) {
      // 第一次展开 — lazy load
      if (diffText === undefined) {
        setLoadingDiff(true);
        setLoadError(null);
        try {
          const text = await onLoad();
          setDiffText(text);
        } catch (e) {
          setLoadError((e as Error).message);
        } finally {
          setLoadingDiff(false);
        }
      }
    }
    setOpen((v) => !v);
  };

  return (
    <li
      style={{
        borderTop: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 6%, transparent)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "6px 10px",
          fontFamily: "var(--theme-font-mono, monospace)",
          fontSize: 12,
          color: "var(--midground, #ffe6cb)",
        }}
      >
        <span
          aria-label={meta.label}
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 18,
            height: 18,
            borderRadius: 3,
            background: meta.bg,
            color: meta.color,
            fontSize: 11,
            fontWeight: 700,
          }}
        >
          {meta.label}
        </span>
        <span
          style={{
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={file.path}
        >
          {file.path}
        </span>
        <span style={{ color: "#34d399" }}>+{file.additions}</span>
        <span style={{ color: "#fb7185" }}>−{file.deletions}</span>
        <button
          onClick={toggle}
          aria-expanded={open}
          disabled={loadingDiff}
          style={{
            marginLeft: 6,
            padding: "1px 6px",
            fontSize: 10,
            border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 18%, transparent)",
            borderRadius: 3,
            background: "transparent",
            color: "var(--color-text-secondary, #6b7280)",
            cursor: loadingDiff ? "wait" : "pointer",
            fontFamily: "inherit",
          }}
        >
          {loadingDiff ? "…" : open ? "Hide" : "Diff"}
        </button>
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="diff"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            style={{
              overflow: "hidden",
              borderTop: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 10%, transparent)",
            }}
          >
            {loadError ? (
              <div
                style={{
                  padding: "8px 12px",
                  fontSize: 11,
                  color: "#fca5a5",
                  background: "color-mix(in srgb, #ef4444 8%, transparent)",
                }}
              >
                load error: {loadError}
              </div>
            ) : diffText === undefined ? (
              <div
                style={{
                  padding: "8px 12px",
                  fontSize: 11,
                  color: "var(--color-text-secondary, #6b7280)",
                }}
              >
                loading…
              </div>
            ) : diffText.trim() === "" ? (
              <div
                style={{
                  padding: "8px 12px",
                  fontSize: 11,
                  color: "var(--color-text-secondary, #6b7280)",
                }}
              >
                no diff content
              </div>
            ) : (
              <DiffRenderer text={diffText} mode={viewMode} />
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  );
}

// 老的内联 UnifiedDiffView / DiffLine 已迁出到 ./diff-renderer.tsx (Wave B)。
