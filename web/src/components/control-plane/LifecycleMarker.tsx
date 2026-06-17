/**
 * LifecycleMarker — session/turn 生命周期事件的轻量标记。
 *
 * 处理事件：
 *   session.started / turn.started / turn.completed / turn.failed / turn.cancelled
 *
 * 不产生大块卡片，只是一行带左侧色点的小标记，让事件流可读。
 */

import { motion } from "motion/react";
import type { EventRecord } from "@/pages/control-plane/types";

const COLORS: Record<string, { dot: string; label: string; tone: string }> = {
  "session.started": { dot: "#10b981", label: "Session started", tone: "#065f46" },
  "turn.started": { dot: "#3b82f6", label: "Turn started", tone: "#1e3a8a" },
  "turn.completed": { dot: "#10b981", label: "Turn completed", tone: "#065f46" },
  "turn.failed": { dot: "#ef4444", label: "Turn failed", tone: "#991b1b" },
  "turn.cancelled": { dot: "#9ca3af", label: "Turn cancelled", tone: "#374151" },
};

export type LifecycleMarkerProps = {
  event: EventRecord;
};

export function LifecycleMarker({ event }: LifecycleMarkerProps) {
  const meta = COLORS[event.type] ?? { dot: "#6b7280", label: event.type, tone: "#374151" };

  // turn.failed 单独渲染 error 一行；其它生命周期事件只显示标题 + 时间
  const error =
    event.type === "turn.failed" && typeof event.payload?.error === "string"
      ? (event.payload.error as string)
      : null;
  const summary =
    event.type === "turn.completed" && typeof event.payload?.summary === "string"
      ? (event.payload.summary as string)
      : null;
  const reason =
    event.type === "turn.cancelled" && typeof event.payload?.reason === "string"
      ? (event.payload.reason as string)
      : null;

  return (
    <motion.div
      layout="position"
      initial={{ opacity: 0, x: -4 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.15, ease: "easeOut" }}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "4px 6px",
        fontSize: 11,
        color: meta.tone,
        fontFamily: "var(--theme-font-mono, monospace)",
      }}
    >
      <span
        aria-hidden
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: meta.dot,
          flexShrink: 0,
        }}
      />
      <span style={{ fontWeight: 600 }}>{meta.label}</span>
      {error && <span style={{ color: "#991b1b" }}>· {error}</span>}
      {summary && <span style={{ color: "#6b7280" }}>· {summary}</span>}
      {reason && <span style={{ color: "#6b7280" }}>· {reason}</span>}
      <span style={{ marginLeft: "auto", color: "#9ca3af" }}>
        {new Date(event.created_at).toLocaleTimeString()}
      </span>
    </motion.div>
  );
}
