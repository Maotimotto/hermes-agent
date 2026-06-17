/**
 * FileChangePanel — 可折叠的文件变更侧面板。
 *
 * 监听 file.changed 事件聚合：按 path 取最新一条（最新 operation/diff 覆盖
 * 同一路径的旧记录），显示 M/A/D 标识 + 路径 + 变更行数。
 *
 * 单条 diff 默认折叠，展开显示前 200 行。
 */

import { useMemo, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import type { EventRecord } from "@/pages/control-plane/types";

type Operation = "create" | "edit" | "delete";

type FileChange = {
  path: string;
  operation: Operation;
  added: number;
  removed: number;
  diff?: string;
  /** Latest event id for this path — used for stable key + dedupe. */
  evtId: number;
};

const OP_META: Record<Operation, { label: string; color: string; bg: string }> = {
  create: { label: "A", color: "#065f46", bg: "#d1fae5" },
  edit: { label: "M", color: "#92400e", bg: "#fef3c7" },
  delete: { label: "D", color: "#991b1b", bg: "#fee2e2" },
};

function countDiffLines(diff?: string): { added: number; removed: number } {
  if (!diff) return { added: 0, removed: 0 };
  let added = 0;
  let removed = 0;
  for (const line of diff.split("\n")) {
    if (line.startsWith("+++") || line.startsWith("---")) continue;
    if (line.startsWith("+")) added += 1;
    else if (line.startsWith("-")) removed += 1;
  }
  return { added, removed };
}

function aggregateFileChanges(events: EventRecord[]): FileChange[] {
  const byPath = new Map<string, FileChange>();
  for (const ev of events) {
    if (ev.type !== "file.changed") continue;
    const p = ev.payload as Record<string, unknown>;
    const path = typeof p.path === "string" ? p.path : "";
    if (!path) continue;
    const operation = (typeof p.operation === "string" ? p.operation : "edit") as Operation;
    const diff = typeof p.diff === "string" ? p.diff : undefined;
    const counts = countDiffLines(diff);
    byPath.set(path, {
      path,
      operation,
      added: counts.added,
      removed: counts.removed,
      diff,
      evtId: ev.id,
    });
  }
  return [...byPath.values()].sort((a, b) => a.path.localeCompare(b.path));
}

export type FileChangePanelProps = {
  events: EventRecord[];
  /** 默认是否展开整个面板。 */
  defaultOpen?: boolean;
};

export function FileChangePanel({ events, defaultOpen = false }: FileChangePanelProps) {
  const changes = useMemo(() => aggregateFileChanges(events), [events]);
  const [open, setOpen] = useState(defaultOpen);

  if (changes.length === 0) return null;

  const totals = changes.reduce(
    (acc, c) => ({ added: acc.added + c.added, removed: acc.removed + c.removed }),
    { added: 0, removed: 0 },
  );

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
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 10px",
          background: "transparent",
          border: "none",
          color: "var(--midground, #ffe6cb)",
          fontFamily: "var(--theme-font-mono, monospace)",
          fontSize: 12,
          cursor: "pointer",
          textAlign: "left",
        }}
      >
        <span aria-hidden style={{ color: "var(--color-text-secondary)" }}>
          {open ? "▾" : "▸"}
        </span>
        <span style={{ fontWeight: 700, letterSpacing: 0.4 }}>FILES CHANGED</span>
        <span style={{ color: "var(--color-text-secondary, #6b7280)" }}>
          {changes.length} file{changes.length === 1 ? "" : "s"}
        </span>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: 8 }}>
          <span style={{ color: "#34d399" }}>+{totals.added}</span>
          <span style={{ color: "#fb7185" }}>−{totals.removed}</span>
        </span>
      </button>

      <AnimatePresence initial={false}>
        {open && (
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
            {changes.map((c) => (
              <FileRow key={c.path} change={c} />
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </motion.section>
  );
}

function FileRow({ change }: { change: FileChange }) {
  const [diffOpen, setDiffOpen] = useState(false);
  const op = OP_META[change.operation];

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
          aria-label={op.label}
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 18,
            height: 18,
            borderRadius: 3,
            background: op.bg,
            color: op.color,
            fontSize: 11,
            fontWeight: 700,
          }}
        >
          {op.label}
        </span>
        <span
          style={{
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={change.path}
        >
          {change.path}
        </span>
        <span style={{ color: "#34d399" }}>+{change.added}</span>
        <span style={{ color: "#fb7185" }}>−{change.removed}</span>
        {change.diff && (
          <button
            onClick={() => setDiffOpen((v) => !v)}
            aria-expanded={diffOpen}
            style={{
              marginLeft: 6,
              padding: "1px 6px",
              fontSize: 10,
              border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 18%, transparent)",
              borderRadius: 3,
              background: "transparent",
              color: "var(--color-text-secondary, #6b7280)",
              cursor: "pointer",
            }}
          >
            {diffOpen ? "Hide" : "Diff"}
          </button>
        )}
      </div>
      <AnimatePresence initial={false}>
        {diffOpen && change.diff && (
          <motion.pre
            key="diff"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            style={{
              margin: 0,
              padding: "8px 12px",
              fontSize: 11,
              lineHeight: 1.45,
              background: "color-mix(in srgb, #000 30%, var(--background-base, #041c1c))",
              color: "#e6e6e6",
              maxHeight: 280,
              overflow: "auto",
              whiteSpace: "pre",
              borderTop: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 10%, transparent)",
            }}
          >
            {truncateDiff(change.diff)}
          </motion.pre>
        )}
      </AnimatePresence>
    </li>
  );
}

function truncateDiff(diff: string, maxLines = 200): string {
  const lines = diff.split("\n");
  if (lines.length <= maxLines) return diff;
  return lines.slice(0, maxLines).join("\n") + `\n… (${lines.length - maxLines} more lines)`;
}
