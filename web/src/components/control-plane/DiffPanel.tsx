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

import { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  useWorkspaceDiff,
  type DiffFileEntry,
} from "@/hooks/control-plane/useWorkspaceDiff";

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
  const {
    files,
    totalAdditions,
    totalDeletions,
    totalFiles,
    loading,
    error,
    unifiedDiffs,
    loadUnified,
    reload,
  } = useWorkspaceDiff(workspaceId);

  if (!workspaceId) return null;
  if (!loading && files.length === 0 && !error) return null;

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
            {totalFiles} file{totalFiles === 1 ? "" : "s"}
          </span>
          <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8 }}>
            <span style={{ color: "#34d399" }}>+{totalAdditions}</span>
            <span style={{ color: "#fb7185" }}>−{totalDeletions}</span>
          </span>
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

      <AnimatePresence initial={false}>
        {open && files.length > 0 && (
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
            {files.map((f) => (
              <DiffFileRow
                key={f.path}
                file={f}
                cachedDiff={unifiedDiffs[f.path]}
                onLoad={() => loadUnified(f.path)}
              />
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </motion.section>
  );
}

// ── Row ────────────────────────────────────────────────────────────────

function DiffFileRow({
  file,
  cachedDiff,
  onLoad,
}: {
  file: DiffFileEntry;
  cachedDiff: string | undefined;
  onLoad: () => Promise<string>;
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
              <UnifiedDiffView text={diffText} />
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  );
}

// ── Unified diff renderer ─────────────────────────────────────────────

const MAX_LINES = 400;

function UnifiedDiffView({ text }: { text: string }) {
  const allLines = text.split("\n");
  const truncated = allLines.length > MAX_LINES;
  const lines = truncated ? allLines.slice(0, MAX_LINES) : allLines;

  return (
    <pre
      style={{
        margin: 0,
        padding: "6px 0",
        fontSize: 11,
        lineHeight: 1.5,
        background: "color-mix(in srgb, #000 30%, var(--background-base, #041c1c))",
        color: "#e6e6e6",
        maxHeight: 360,
        overflow: "auto",
        whiteSpace: "pre",
      }}
    >
      {lines.map((line, i) => (
        <DiffLine key={i} line={line} />
      ))}
      {truncated && (
        <div
          style={{
            padding: "4px 12px",
            fontSize: 10,
            color: "#9ca3af",
            fontStyle: "italic",
          }}
        >
          … truncated, {allLines.length - MAX_LINES} more lines
        </div>
      )}
    </pre>
  );
}

function DiffLine({ line }: { line: string }) {
  let bg = "transparent";
  let color = "#e6e6e6";
  if (line.startsWith("+++") || line.startsWith("---")) {
    color = "#9ca3af";
  } else if (line.startsWith("@@")) {
    bg = "color-mix(in srgb, #6366f1 12%, transparent)";
    color = "#a5b4fc";
  } else if (line.startsWith("+")) {
    bg = "color-mix(in srgb, #10b981 12%, transparent)";
    color = "#6ee7b7";
  } else if (line.startsWith("-")) {
    bg = "color-mix(in srgb, #ef4444 14%, transparent)";
    color = "#fca5a5";
  } else if (line.startsWith("diff ") || line.startsWith("index ")) {
    color = "#9ca3af";
  }
  return (
    <div style={{ background: bg, color, padding: "0 12px" }}>{line || "\u00A0"}</div>
  );
}
