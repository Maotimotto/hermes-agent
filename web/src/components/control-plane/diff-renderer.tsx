/**
 * diff-renderer — Unified / Split 两种渲染模式 + 行号 + 复制按钮。
 *
 * 输入：原始 unified diff 文本（git diff -u 输出）。
 * 输出：JSX 块。两种模式共享同一份解析（``parseHunks``）。
 *
 * 设计取舍：
 *   - 不引入 react-diff-view 等大库 — 自己解析够用，控制平面 diff 体量小。
 *   - 单文件 diff 多 hunk 时按 hunk 串行渲染，hunk 间留分隔。
 *   - 行号严格按 ``@@ -a,b +c,d @@`` 推进；context/+/- 各自正确递增。
 *   - 截断：每个 file 展示前 MAX_LINES_PER_FILE 行（两种模式都算总行数）。
 */

import { useMemo, useState } from "react";

export type DiffViewMode = "unified" | "split";

const MAX_LINES_PER_FILE = 600;

// ── parsing ──────────────────────────────────────────────────────────

type Hunk = {
  header: string;
  oldStart: number;
  newStart: number;
  lines: HunkLine[];
};

type HunkLine = {
  kind: "context" | "add" | "del" | "meta";
  text: string;
  oldNum: number | null;
  newNum: number | null;
};

const HUNK_RE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

function parseHunks(diff: string): { headers: string[]; hunks: Hunk[] } {
  const lines = diff.split("\n");
  const headers: string[] = [];
  const hunks: Hunk[] = [];
  let cur: Hunk | null = null;
  let oldCounter = 0;
  let newCounter = 0;

  for (const raw of lines) {
    if (
      raw.startsWith("diff ") ||
      raw.startsWith("index ") ||
      raw.startsWith("--- ") ||
      raw.startsWith("+++ ") ||
      raw.startsWith("new file mode") ||
      raw.startsWith("deleted file mode") ||
      raw.startsWith("rename ") ||
      raw.startsWith("similarity ")
    ) {
      if (!cur) headers.push(raw);
      // 进入 hunk 后再遇到这种通常意味着新文件 — 控制平面单文件接口不该出现
      continue;
    }
    const m = HUNK_RE.exec(raw);
    if (m) {
      cur = {
        header: raw,
        oldStart: parseInt(m[1], 10),
        newStart: parseInt(m[2], 10),
        lines: [],
      };
      oldCounter = cur.oldStart;
      newCounter = cur.newStart;
      hunks.push(cur);
      continue;
    }
    if (!cur) continue;
    if (raw.startsWith("+")) {
      cur.lines.push({
        kind: "add",
        text: raw.slice(1),
        oldNum: null,
        newNum: newCounter,
      });
      newCounter += 1;
    } else if (raw.startsWith("-")) {
      cur.lines.push({
        kind: "del",
        text: raw.slice(1),
        oldNum: oldCounter,
        newNum: null,
      });
      oldCounter += 1;
    } else if (raw.startsWith("\\")) {
      // \ No newline at end of file — 元信息，不计行号
      cur.lines.push({ kind: "meta", text: raw, oldNum: null, newNum: null });
    } else {
      // context line（开头空格或 git 老版本无前缀）
      const text = raw.startsWith(" ") ? raw.slice(1) : raw;
      cur.lines.push({
        kind: "context",
        text,
        oldNum: oldCounter,
        newNum: newCounter,
      });
      oldCounter += 1;
      newCounter += 1;
    }
  }

  return { headers, hunks };
}

// ── pairing for split view ──────────────────────────────────────────

type SplitRow =
  | { kind: "context"; left: HunkLine; right: HunkLine }
  | { kind: "del"; left: HunkLine; right: null }
  | { kind: "add"; left: null; right: HunkLine }
  | { kind: "change"; left: HunkLine; right: HunkLine }
  | { kind: "meta"; left: HunkLine | null; right: HunkLine | null };

function pairHunkForSplit(h: Hunk): SplitRow[] {
  const rows: SplitRow[] = [];
  let i = 0;
  while (i < h.lines.length) {
    const line = h.lines[i];
    if (line.kind === "context") {
      rows.push({ kind: "context", left: line, right: line });
      i += 1;
      continue;
    }
    if (line.kind === "meta") {
      rows.push({ kind: "meta", left: line, right: line });
      i += 1;
      continue;
    }
    // 找 - 后紧跟的 + 段做配对（典型 modify hunk 形态）
    if (line.kind === "del") {
      const dels: HunkLine[] = [];
      while (i < h.lines.length && h.lines[i].kind === "del") {
        dels.push(h.lines[i]);
        i += 1;
      }
      const adds: HunkLine[] = [];
      while (i < h.lines.length && h.lines[i].kind === "add") {
        adds.push(h.lines[i]);
        i += 1;
      }
      const max = Math.max(dels.length, adds.length);
      for (let k = 0; k < max; k++) {
        const d = dels[k];
        const a = adds[k];
        if (d && a) rows.push({ kind: "change", left: d, right: a });
        else if (d) rows.push({ kind: "del", left: d, right: null });
        else if (a) rows.push({ kind: "add", left: null, right: a });
      }
      continue;
    }
    if (line.kind === "add") {
      // 纯新增段（前面没 -）— 全部当 add
      while (i < h.lines.length && h.lines[i].kind === "add") {
        rows.push({ kind: "add", left: null, right: h.lines[i] });
        i += 1;
      }
      continue;
    }
    i += 1;
  }
  return rows;
}

// ── colors ─────────────────────────────────────────────────────────

const KIND_BG: Record<HunkLine["kind"], string> = {
  context: "transparent",
  add: "color-mix(in srgb, #10b981 12%, transparent)",
  del: "color-mix(in srgb, #ef4444 14%, transparent)",
  meta: "transparent",
};
const KIND_FG: Record<HunkLine["kind"], string> = {
  context: "#e6e6e6",
  add: "#6ee7b7",
  del: "#fca5a5",
  meta: "#9ca3af",
};
const NUM_FG = "#6b7280";
const HUNK_BG = "color-mix(in srgb, #6366f1 12%, transparent)";
const HUNK_FG = "#a5b4fc";

// ── public components ─────────────────────────────────────────────

export function DiffViewSwitcher({
  mode,
  onChange,
}: {
  mode: DiffViewMode;
  onChange: (m: DiffViewMode) => void;
}) {
  const segStyle = (active: boolean): React.CSSProperties => ({
    padding: "2px 8px",
    fontSize: 10,
    border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 18%, transparent)",
    background: active
      ? "color-mix(in srgb, var(--midground-base, #ffe6cb) 16%, transparent)"
      : "transparent",
    color: active ? "var(--midground, #ffe6cb)" : "var(--color-text-secondary, #6b7280)",
    cursor: "pointer",
    fontFamily: "inherit",
  });
  return (
    <div style={{ display: "inline-flex", borderRadius: 3, overflow: "hidden" }}>
      <button
        type="button"
        style={{ ...segStyle(mode === "unified"), borderRadius: "3px 0 0 3px" }}
        onClick={() => onChange("unified")}
      >
        Unified
      </button>
      <button
        type="button"
        style={{ ...segStyle(mode === "split"), borderRadius: "0 3px 3px 0", borderLeft: "none" }}
        onClick={() => onChange("split")}
      >
        Split
      </button>
    </div>
  );
}

export function DiffRenderer({
  text,
  mode,
}: {
  text: string;
  mode: DiffViewMode;
}) {
  const parsed = useMemo(() => parseHunks(text), [text]);
  const totalLines = parsed.hunks.reduce((acc, h) => acc + h.lines.length, 0);

  if (parsed.hunks.length === 0) {
    return (
      <div
        style={{
          padding: "8px 12px",
          fontSize: 11,
          color: "#9ca3af",
        }}
      >
        no hunks
      </div>
    );
  }

  // 全文截断 — 累计行数到 MAX_LINES_PER_FILE 截止
  const displayHunks: Hunk[] = [];
  let used = 0;
  let overflow = 0;
  for (const h of parsed.hunks) {
    if (used + h.lines.length <= MAX_LINES_PER_FILE) {
      displayHunks.push(h);
      used += h.lines.length;
    } else {
      const left = MAX_LINES_PER_FILE - used;
      if (left > 0) {
        displayHunks.push({ ...h, lines: h.lines.slice(0, left) });
        used += left;
      }
      overflow = totalLines - used;
      break;
    }
  }

  return (
    <div
      style={{
        background: "color-mix(in srgb, #000 30%, var(--background-base, #041c1c))",
        maxHeight: 480,
        overflow: "auto",
        fontFamily: "var(--theme-font-mono, monospace)",
        fontSize: 11,
        lineHeight: 1.55,
      }}
    >
      <div style={{ position: "sticky", top: 0, zIndex: 1 }}>
        <CopyBar text={text} />
      </div>
      {displayHunks.map((h, i) =>
        mode === "unified" ? (
          <UnifiedHunk key={i} hunk={h} />
        ) : (
          <SplitHunk key={i} hunk={h} />
        ),
      )}
      {overflow > 0 && (
        <div
          style={{
            padding: "4px 12px",
            fontSize: 10,
            color: "#9ca3af",
            fontStyle: "italic",
            borderTop: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 8%, transparent)",
          }}
        >
          … truncated, {overflow} more lines
        </div>
      )}
    </div>
  );
}

// ── unified hunk ──────────────────────────────────────────────────

function UnifiedHunk({ hunk }: { hunk: Hunk }) {
  return (
    <div>
      <div style={{ background: HUNK_BG, color: HUNK_FG, padding: "1px 12px" }}>
        {hunk.header}
      </div>
      {hunk.lines.map((line, i) => (
        <div
          key={i}
          style={{
            display: "grid",
            gridTemplateColumns: "44px 44px 14px 1fr",
            background: KIND_BG[line.kind],
            color: KIND_FG[line.kind],
          }}
        >
          <span style={lineNumStyle}>{line.oldNum ?? ""}</span>
          <span style={lineNumStyle}>{line.newNum ?? ""}</span>
          <span style={signStyle}>
            {line.kind === "add" ? "+" : line.kind === "del" ? "-" : line.kind === "meta" ? "" : " "}
          </span>
          <span style={textStyle}>{line.text || "\u00A0"}</span>
        </div>
      ))}
    </div>
  );
}

// ── split hunk ─────────────────────────────────────────────────────

function SplitHunk({ hunk }: { hunk: Hunk }) {
  const rows = useMemo(() => pairHunkForSplit(hunk), [hunk]);
  return (
    <div>
      <div style={{ background: HUNK_BG, color: HUNK_FG, padding: "1px 12px" }}>
        {hunk.header}
      </div>
      {rows.map((row, i) => {
        const left = row.left;
        const right = row.right;
        const leftKind = left ? left.kind : "context";
        const rightKind = right ? right.kind : "context";
        // change 行：左 del 右 add 的视觉
        const leftBg = left ? KIND_BG[leftKind] : "color-mix(in srgb, #000 20%, transparent)";
        const rightBg = right ? KIND_BG[rightKind] : "color-mix(in srgb, #000 20%, transparent)";
        const leftFg = left ? KIND_FG[leftKind] : "#9ca3af";
        const rightFg = right ? KIND_FG[rightKind] : "#9ca3af";
        return (
          <div
            key={i}
            style={{
              display: "grid",
              gridTemplateColumns: "44px 1fr 44px 1fr",
              borderBottom: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 4%, transparent)",
            }}
          >
            <span style={{ ...lineNumStyle, background: leftBg }}>{left?.oldNum ?? ""}</span>
            <span style={{ ...textStyle, background: leftBg, color: leftFg }}>
              {left ? left.text || "\u00A0" : "\u00A0"}
            </span>
            <span style={{ ...lineNumStyle, background: rightBg }}>{right?.newNum ?? ""}</span>
            <span style={{ ...textStyle, background: rightBg, color: rightFg }}>
              {right ? right.text || "\u00A0" : "\u00A0"}
            </span>
          </div>
        );
      })}
    </div>
  );
}

const lineNumStyle: React.CSSProperties = {
  padding: "0 6px",
  textAlign: "right",
  color: NUM_FG,
  userSelect: "none",
  fontVariantNumeric: "tabular-nums",
};
const signStyle: React.CSSProperties = {
  padding: "0 2px",
  textAlign: "center",
  userSelect: "none",
};
const textStyle: React.CSSProperties = {
  padding: "0 8px",
  whiteSpace: "pre",
  overflow: "hidden",
  textOverflow: "ellipsis",
};

// ── copy bar ──────────────────────────────────────────────────────

function CopyBar({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // fallback: 创建临时 textarea
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        setCopied(true);
        setTimeout(() => setCopied(false), 1200);
      } catch {
        /* noop */
      }
      document.body.removeChild(ta);
    }
  };
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "flex-end",
        padding: "4px 8px",
        background: "color-mix(in srgb, #000 50%, var(--background-base, #041c1c))",
        borderBottom: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 8%, transparent)",
      }}
    >
      <button
        type="button"
        onClick={onCopy}
        style={{
          padding: "1px 8px",
          fontSize: 10,
          border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 18%, transparent)",
          borderRadius: 3,
          background: "transparent",
          color: copied ? "#6ee7b7" : "var(--color-text-secondary, #9ca3af)",
          cursor: "pointer",
          fontFamily: "var(--theme-font-mono, monospace)",
        }}
      >
        {copied ? "Copied ✓" : "Copy"}
      </button>
    </div>
  );
}
