/**
 * AssistantBubble — 渲染 assistant 输出（message/delta 累积/thinking）。
 *
 * 接收聚合后的文本块（来自 useTimelineGroups），父级负责把多个 delta
 * 拼接到同一气泡。thinking 显示为低对比度斜体段落，可与正文同框，
 * 便于观察 reasoning trace。
 */

import { motion } from "motion/react";

export type AssistantBubbleProps = {
  text: string;
  thinking?: string;
  /** 是否还在流式中（最后一块 delta 之后还没来 message）。 */
  streaming?: boolean;
  timestamp?: string;
};

export function AssistantBubble({
  text,
  thinking,
  streaming,
  timestamp,
}: AssistantBubbleProps) {
  return (
    <motion.div
      layout="position"
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      style={{
        margin: "8px 0",
        padding: "10px 12px",
        background: "color-mix(in srgb, var(--midground-base, #ffe6cb) 6%, var(--background-base, #041c1c))",
        border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 18%, transparent)",
        borderRadius: 8,
        boxShadow: "0 1px 2px rgba(0,0,0,0.08)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          fontSize: 11,
          color: "var(--color-text-secondary, #6b7280)",
          marginBottom: 6,
        }}
      >
        <span style={{ fontWeight: 600, letterSpacing: 0.4 }}>ASSISTANT</span>
        {timestamp && <span>{new Date(timestamp).toLocaleTimeString()}</span>}
      </div>

      {thinking && (
        <div
          style={{
            margin: "0 0 8px",
            padding: "6px 8px",
            borderLeft: "2px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 30%, transparent)",
            fontStyle: "italic",
            fontSize: 12,
            color: "var(--color-text-secondary, #6b7280)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {thinking}
        </div>
      )}

      <div
        style={{
          fontSize: 13,
          lineHeight: 1.6,
          color: "var(--midground, #ffe6cb)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {text}
        {streaming && (
          <motion.span
            aria-hidden
            animate={{ opacity: [0.2, 1, 0.2] }}
            transition={{ duration: 1.2, repeat: Infinity, ease: "linear" }}
            style={{
              display: "inline-block",
              width: 6,
              height: 12,
              marginLeft: 2,
              background: "currentColor",
              verticalAlign: "text-bottom",
              borderRadius: 1,
            }}
          />
        )}
      </div>
    </motion.div>
  );
}
