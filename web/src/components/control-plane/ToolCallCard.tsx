/**
 * ToolCallCard — 单个工具调用卡片（聚合 tool.started/output/completed）。
 *
 * 默认折叠：只显示 header（图标 + tool name + status + 耗时）。
 * 点击展开：显示 input（JSON）+ output（终端风格等宽文本）。
 *
 * 聚合在 useTimelineGroups 完成；本组件只接收已组装好的 ToolGroup。
 */

import { useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import { XtermViewer } from "./XtermViewer";

export type ToolStatus = "running" | "ok" | "error";

export type ToolGroup = {
  id: string;
  toolCallId: string;
  toolName: string;
  status: ToolStatus;
  input: unknown;
  output: string;
  duration?: number | null;
  error?: string | null;
  startedAt?: string;
};

const STATUS_ICON: Record<ToolStatus, { dot: string; label: string }> = {
  running: { dot: "#3b82f6", label: "Running" },
  ok: { dot: "#10b981", label: "OK" },
  error: { dot: "#ef4444", label: "Error" },
};

export type ToolCallCardProps = {
  group: ToolGroup;
};

export function ToolCallCard({ group }: ToolCallCardProps) {
  const [open, setOpen] = useState(false);
  const [termMode, setTermMode] = useState(false);
  const meta = STATUS_ICON[group.status];

  return (
    <motion.div
      layout="position"
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      style={{
        margin: "6px 0",
        border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 14%, transparent)",
        borderRadius: 8,
        background: "color-mix(in srgb, var(--midground-base, #ffe6cb) 3%, var(--background-base, #041c1c))",
        overflow: "hidden",
      }}
    >
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 10px",
          background: "transparent",
          color: "var(--midground, #ffe6cb)",
          border: "none",
          textAlign: "left",
          cursor: "pointer",
          fontFamily: "var(--theme-font-mono, monospace)",
          fontSize: 12,
        }}
        aria-expanded={open}
      >
        <span
          aria-hidden
          style={{ fontSize: 13, lineHeight: 1, color: "var(--color-text-secondary)" }}
        >
          {open ? "▾" : "▸"}
        </span>
        <span aria-hidden style={{ fontSize: 13 }}>⚒</span>
        <span style={{ fontWeight: 700 }}>{group.toolName || "tool"}</span>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            color: "var(--color-text-secondary, #6b7280)",
          }}
        >
          <span
            aria-hidden
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: meta.dot,
            }}
          />
          {meta.label}
        </span>
        {typeof group.duration === "number" && group.duration >= 0 && (
          <span style={{ color: "var(--color-text-secondary, #6b7280)" }}>
            · {group.duration}ms
          </span>
        )}
        <span
          style={{
            marginLeft: "auto",
            color: "var(--color-text-secondary, #6b7280)",
            fontSize: 11,
          }}
          title={group.toolCallId}
        >
          {group.toolCallId.slice(0, 8)}
        </span>
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            style={{ overflow: "hidden" }}
          >
            <div
              style={{
                padding: "0 10px 10px",
                borderTop: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 10%, transparent)",
              }}
            >
              {group.input !== undefined && group.input !== null && (
                <Section label="input">
                  <pre
                    style={{
                      margin: 0,
                      fontSize: 11,
                      color: "var(--midground, #ffe6cb)",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                    }}
                  >
                    {typeof group.input === "string"
                      ? group.input
                      : JSON.stringify(group.input, null, 2)}
                  </pre>
                </Section>
              )}
              {group.output && (
                <Section
                  label="output"
                  rightSlot={
                    <button
                      onClick={() => setTermMode((m) => !m)}
                      style={{
                        fontSize: 10,
                        padding: "2px 6px",
                        background: "transparent",
                        border: "1px solid var(--color-text-secondary, #6b7280)",
                        borderRadius: 3,
                        color: "var(--color-text-secondary, #6b7280)",
                        cursor: "pointer",
                      }}
                      title={termMode ? "切回文本视图" : "用 xterm 渲染（保留 ANSI 颜色）"}
                    >
                      {termMode ? "📄 文本" : "📺 终端"}
                    </button>
                  }
                >
                  {termMode ? (
                    <XtermViewer data={group.output} height={280} />
                  ) : (
                    <pre
                      style={{
                        margin: 0,
                        padding: "8px 10px",
                        background: "color-mix(in srgb, #000 30%, var(--background-base, #041c1c))",
                        borderRadius: 4,
                        fontSize: 11,
                        lineHeight: 1.5,
                        color: "#e6e6e6",
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                        maxHeight: 280,
                        overflow: "auto",
                      }}
                    >
                      {group.output}
                    </pre>
                  )}
                </Section>
              )}
              {group.error && (
                <Section label="error">
                  <div
                    style={{
                      padding: "6px 10px",
                      background: "rgba(239,68,68,0.12)",
                      border: "1px solid #fca5a5",
                      borderRadius: 4,
                      color: "#fecaca",
                      fontSize: 12,
                    }}
                  >
                    {group.error}
                  </div>
                </Section>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function Section({
  label,
  children,
  rightSlot,
}: {
  label: string;
  children: React.ReactNode;
  rightSlot?: React.ReactNode;
}) {
  return (
    <div style={{ marginTop: 8 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          fontSize: 10,
          letterSpacing: 0.5,
          textTransform: "uppercase",
          color: "var(--color-text-secondary, #6b7280)",
          marginBottom: 4,
        }}
      >
        <span>{label}</span>
        {rightSlot}
      </div>
      {children}
    </div>
  );
}
