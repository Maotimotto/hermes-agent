/**
 * LifecycleMarker — session/turn 生命周期事件的轻量标记。
 *
 * 处理事件：
 *   session.started / turn.started / turn.completed / turn.failed /
 *   turn.cancelled / turn.retrying
 *
 * 不产生大块卡片，只是一行带左侧色点的小标记，让事件流可读。
 *
 * V1.1 错误恢复 Wave D 增加 turn.retrying 黄色重试条 — 显示
 * attempt/2 + 退避时长 + reason，让用户看到「自动重试中」而不是「失败」。
 */

import { motion } from "motion/react";
import type { EventRecord } from "@/pages/control-plane/types";

const COLORS: Record<string, { dot: string; label: string; tone: string }> = {
  "session.started": { dot: "#10b981", label: "Session started", tone: "#065f46" },
  "turn.started": { dot: "#3b82f6", label: "Turn started", tone: "#1e3a8a" },
  "turn.completed": { dot: "#10b981", label: "Turn completed", tone: "#065f46" },
  "turn.failed": { dot: "#ef4444", label: "Turn failed", tone: "#991b1b" },
  "turn.cancelled": { dot: "#9ca3af", label: "Turn cancelled", tone: "#374151" },
  "turn.retrying": { dot: "#f59e0b", label: "Turn retrying", tone: "#92400e" },
};

export type LifecycleMarkerProps = {
  event: EventRecord;
};

function asString(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function asNumber(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function LifecycleMarker({ event }: LifecycleMarkerProps) {
  const meta = COLORS[event.type] ?? { dot: "#6b7280", label: event.type, tone: "#374151" };

  // turn.failed: error / code
  const error = event.type === "turn.failed" ? asString(event.payload?.error) : "";
  const failedCode = event.type === "turn.failed" ? asString(event.payload?.code) : "";

  // turn.completed: summary
  const summary = event.type === "turn.completed" ? asString(event.payload?.summary) : "";

  // turn.cancelled: reason
  const reason = event.type === "turn.cancelled" ? asString(event.payload?.reason) : "";

  // turn.retrying: attempt / reason / backoff_ms
  const retryAttempt =
    event.type === "turn.retrying" ? asNumber(event.payload?.attempt) : null;
  const retryReason =
    event.type === "turn.retrying" ? asString(event.payload?.reason) : "";
  const retryBackoff =
    event.type === "turn.retrying" ? asNumber(event.payload?.backoff_ms) : null;

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
      {error && (
        <span style={{ color: "#991b1b" }}>
          ·{failedCode ? ` [${failedCode}]` : ""} {error}
        </span>
      )}
      {summary && <span style={{ color: "#6b7280" }}>· {summary}</span>}
      {reason && <span style={{ color: "#6b7280" }}>· {reason}</span>}
      {event.type === "turn.retrying" && (
        <>
          {retryAttempt !== null && (
            <span
              style={{
                padding: "1px 6px",
                borderRadius: 3,
                background: "color-mix(in srgb, #f59e0b 22%, transparent)",
                color: "#92400e",
                fontWeight: 700,
              }}
            >
              attempt {retryAttempt}/2
            </span>
          )}
          {retryReason && (
            <span style={{ color: "#92400e" }}>· {retryReason}</span>
          )}
          {retryBackoff !== null && retryBackoff > 0 && (
            <span style={{ color: "#9ca3af" }}>
              · retry in {Math.round(retryBackoff)}ms
            </span>
          )}
        </>
      )}
      <span style={{ marginLeft: "auto", color: "#9ca3af" }}>
        {new Date(event.created_at).toLocaleTimeString()}
      </span>
    </motion.div>
  );
}
