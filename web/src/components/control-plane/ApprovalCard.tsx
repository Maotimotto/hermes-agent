/**
 * ApprovalCard — 待审批操作的单卡片，提供 Allow/Deny 决策按钮。
 *
 * Wave 1 升级：风险等级用 RiskBadge 渲染（来自后端 risk 字段，
 * 见 agent/control_plane/approval/types.py::RiskLevel）；critical
 * 风险 deny 按钮拿到主色，高亮警示。
 *
 * decide 回调对应 useApprovals().decide(approvalId, decision)。
 */

import { motion } from "motion/react";
import type {
  ApprovalDecision,
  ApprovalRecord,
} from "@/pages/control-plane/types";
import { RiskBadge } from "./RiskBadge";

export type ApprovalCardProps = {
  approval: ApprovalRecord;
  onDecide: (
    approvalId: string,
    decision: Exclude<ApprovalDecision, "pending">,
  ) => void;
};

export function ApprovalCard({ approval, onDecide }: ApprovalCardProps) {
  const risk = (approval as ApprovalRecord & { risk?: string | null }).risk;
  const critical = (risk || "").toLowerCase() === "critical";

  // critical 时整体 tone 偏红，否则保持 amber 提示
  const tone = critical
    ? { border: "#fca5a5", bg: "#fee2e2", title: "#991b1b" }
    : { border: "#fbbf24", bg: "#fef3c7", title: "#92400e" };

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -12 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
      style={{
        padding: 10,
        marginBottom: 8,
        border: `1px solid ${tone.border}`,
        background: tone.bg,
        borderRadius: 6,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 6,
        }}
      >
        <RiskBadge risk={risk} compact />
        <span style={{ fontSize: 12, fontWeight: 600, color: tone.title }}>
          {approval.action_kind}
        </span>
      </div>
      <pre
        style={{
          margin: "6px 0",
          fontSize: 11,
          color: "#374151",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          maxHeight: 100,
          overflow: "auto",
          fontFamily: "var(--theme-font-mono, monospace)",
        }}
      >
        {JSON.stringify(approval.action_payload, null, 2)}
      </pre>
      <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
        <button
          onClick={() => onDecide(approval.id, "approved")}
          style={{
            flex: 1,
            padding: "6px 12px",
            border: "none",
            borderRadius: 4,
            background: critical ? "#a7f3d0" : "#10b981",
            color: critical ? "#065f46" : "white",
            cursor: "pointer",
            fontSize: 12,
            fontWeight: 600,
          }}
        >
          Allow
        </button>
        <button
          onClick={() => onDecide(approval.id, "denied")}
          style={{
            flex: 1,
            padding: "6px 12px",
            border: "none",
            borderRadius: 4,
            background: "#ef4444",
            color: "white",
            cursor: "pointer",
            fontSize: 12,
            fontWeight: 600,
            boxShadow: critical ? "0 0 0 2px rgba(239,68,68,0.35)" : "none",
          }}
        >
          Deny
        </button>
      </div>
    </motion.div>
  );
}
