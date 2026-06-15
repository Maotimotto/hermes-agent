/**
 * SessionCard — sessions 列表中的单行。
 *
 * 自维护 hover 状态用于细微样式变化；当 selected 且 session 未停止时，右上角
 * 显示删除按钮。事件回调由父组件传入（onSelect / onDelete）。
 */

import { useState } from "react";
import { motion } from "motion/react";
import type { SessionRecord } from "@/pages/control-plane/types";
import { STATUS_COLORS } from "@/pages/control-plane/types";

export type SessionCardProps = {
  session: SessionRecord;
  selected: boolean;
  onSelect: (sid: string) => void;
  onDelete: (sid: string) => void;
};

export function SessionCard({
  session,
  selected,
  onSelect,
  onDelete,
}: SessionCardProps) {
  const [hover, setHover] = useState(false);
  const canDelete = selected && session.status !== "stopped";
  const bg = selected ? "#eff6ff" : hover ? "#f9fafb" : "white";

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 8 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      onClick={() => onSelect(session.id)}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        padding: "10px 12px",
        borderBottom: "1px solid #f3f4f6",
        cursor: "pointer",
        background: bg,
        position: "relative",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontSize: 13,
          fontFamily: "monospace",
        }}
      >
        <span>{session.id.slice(0, 16)}</span>
        <span
          style={{
            color: STATUS_COLORS[session.status] || "#6b7280",
            fontWeight: 600,
          }}
        >
          {session.status}
        </span>
      </div>
      <div style={{ fontSize: 12, color: "#6b7280", marginTop: 4 }}>
        {session.runtime_kind} · {session.model}
      </div>
      {session.repo_path && (
        <div
          style={{
            fontSize: 11,
            color: "#9ca3af",
            marginTop: 2,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={session.repo_path}
        >
          📁 {session.repo_path}
        </div>
      )}
      {canDelete && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete(session.id);
          }}
          title="Stop / delete session"
          style={{
            position: "absolute",
            top: 8,
            right: 8,
            width: 22,
            height: 22,
            border: "1px solid #fca5a5",
            background: "white",
            color: "#ef4444",
            borderRadius: 4,
            cursor: "pointer",
            fontSize: 11,
            padding: 0,
          }}
        >
          ✕
        </button>
      )}
    </motion.div>
  );
}
