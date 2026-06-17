/**
 * RiskBadge — Approval 风险等级徽标。
 *
 * 颜色映射对齐 `agent/control_plane/approval/types.py::RiskLevel`：
 *   low      → 绿（informational）
 *   medium   → 黄（caution）
 *   high     → 橙（warn）
 *   critical → 红（dangerous, destructive）
 *
 * 未知或缺失风险时降级到中性灰，避免炸裂。
 */

import type { CSSProperties } from "react";

export type RiskLevel = "low" | "medium" | "high" | "critical";

const RISK_STYLES: Record<RiskLevel, { fg: string; bg: string; border: string; label: string }> = {
  low: { fg: "#065f46", bg: "#d1fae5", border: "#34d399", label: "LOW" },
  medium: { fg: "#92400e", bg: "#fef3c7", border: "#fbbf24", label: "MEDIUM" },
  high: { fg: "#9a3412", bg: "#ffedd5", border: "#fb923c", label: "HIGH" },
  critical: { fg: "#991b1b", bg: "#fee2e2", border: "#f87171", label: "CRITICAL" },
};

const FALLBACK = { fg: "#374151", bg: "#f3f4f6", border: "#d1d5db", label: "UNKNOWN" };

export type RiskBadgeProps = {
  risk?: string | null;
  /** 紧凑模式：去掉 padding，只保留 4 个色块条 + 文字。 */
  compact?: boolean;
  style?: CSSProperties;
};

export function RiskBadge({ risk, compact, style }: RiskBadgeProps) {
  const key = (risk || "").toLowerCase() as RiskLevel;
  const s = RISK_STYLES[key] ?? FALLBACK;

  // 4 段进度块表示风险高度（low=1, medium=2, high=3, critical=4）
  const filled =
    key === "low" ? 1 : key === "medium" ? 2 : key === "high" ? 3 : key === "critical" ? 4 : 0;

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: compact ? "1px 6px" : "2px 8px",
        border: `1px solid ${s.border}`,
        background: s.bg,
        color: s.fg,
        borderRadius: 4,
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: 0.4,
        fontFamily: "var(--theme-font-mono, monospace)",
        ...style,
      }}
      title={`Risk: ${s.label}`}
    >
      <span aria-hidden style={{ display: "inline-flex", gap: 2 }}>
        {[0, 1, 2, 3].map((i) => (
          <span
            key={i}
            style={{
              width: 6,
              height: 8,
              borderRadius: 1,
              background: i < filled ? s.border : "rgba(0,0,0,0.10)",
            }}
          />
        ))}
      </span>
      {s.label}
    </span>
  );
}
