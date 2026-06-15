/**
 * WsStatusBadge — 显示事件流当前传输状态（WS open / polling / closed / …）。
 *
 * 颜色对照 useEventStream 的 WsStatus：
 *  - open       → 绿色，实时 WS
 *  - polling    → 琥珀色，HTTP 3s 轮询兜底
 *  - connecting → 蓝色，握手中
 *  - closed     → 灰色，连接已关
 *  - idle       → 灰色，未选 session
 */

import { motion } from "motion/react";
import type { WsStatus } from "@/pages/control-plane/types";

type Variant = {
  label: string;
  tooltip: string;
  bg: string;
  fg: string;
};

const VARIANTS: Record<WsStatus, Variant> = {
  open: {
    label: "● live",
    tooltip: "WebSocket 实时推送中",
    bg: "#10b98120",
    fg: "#10b981",
  },
  polling: {
    label: "◐ poll 3s",
    tooltip: "WS 不可用，已降级到 3s 轮询",
    bg: "#f59e0b20",
    fg: "#f59e0b",
  },
  connecting: {
    label: "○ connecting",
    tooltip: "正在建立 WebSocket 连接",
    bg: "#3b82f620",
    fg: "#3b82f6",
  },
  closed: {
    label: "× closed",
    tooltip: "WebSocket 已关闭",
    bg: "#9ca3af20",
    fg: "#6b7280",
  },
  idle: {
    label: "idle",
    tooltip: "未连接",
    bg: "#9ca3af20",
    fg: "#6b7280",
  },
};

export function WsStatusBadge({ status }: { status: WsStatus }) {
  const v = VARIANTS[status];
  return (
    <motion.span
      key={status}
      initial={{ opacity: 0, scale: 0.85 }}
      animate={
        status === "open"
          ? { opacity: [0.7, 1, 0.7], scale: 1 }
          : { opacity: 1, scale: 1 }
      }
      transition={
        status === "open"
          ? { opacity: { duration: 2, repeat: Infinity, ease: "easeInOut" }, scale: { duration: 0.2 } }
          : { duration: 0.2 }
      }
      title={v.tooltip}
      style={{
        display: "inline-block",
        marginLeft: 10,
        fontSize: 11,
        padding: "1px 6px",
        borderRadius: 999,
        background: v.bg,
        color: v.fg,
      }}
    >
      {v.label}
    </motion.span>
  );
}
