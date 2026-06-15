/**
 * ApprovalCard — 待审批操作的单卡片，提供 Allow/Deny 决策按钮。
 *
 * decide 回调对应 useApprovals().decide(approvalId, decision)。
 */

import { motion } from "motion/react";
import type {
  ApprovalDecision,
  ApprovalRecord,
} from "@/pages/control-plane/types";

export type ApprovalCardProps = {
  approval: ApprovalRecord;
  onDecide: (
    approvalId: string,
    decision: Exclude<ApprovalDecision, "pending">,
  ) => void;
};

export function ApprovalCard({ approval, onDecide }: ApprovalCardProps) {
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
        border: "1px solid #fbbf24",
        background: "#fef3c7",
        borderRadius: 6,
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 600, color: "#92400e" }}>
        {approval.action_kind}
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
            background: "#10b981",
            color: "white",
            cursor: "pointer",
            fontSize: 12,
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
          }}
        >
          Deny
        </button>
      </div>
    </motion.div>
  );
}
