/**
 * EventTimeline — 选中 session 的事件流渲染。
 *
 * 路书 10 §6 设计：通过 useTimelineGroups 把扁平事件聚合成 4 类渲染单元
 * （lifecycle / assistant / tool / raw），分别交给对应组件。file.changed
 * 由 FileChangePanel 单独消费，approval.* 由右侧 Pending Approvals 面板
 * 处理，timeline 这里不再重复渲染。
 *
 * 空态根据上下文显示「未选 session」/「无事件」两种态。
 */

import { AnimatePresence, motion } from "motion/react";
import type { EventRecord } from "@/pages/control-plane/types";
import { useTimelineGroups } from "@/hooks/control-plane/useTimelineGroups";
import { LifecycleMarker } from "./LifecycleMarker";
import { AssistantBubble } from "./AssistantBubble";
import { ToolCallCard } from "./ToolCallCard";

export type EventTimelineProps = {
  events: EventRecord[];
  hasSelection: boolean;
};

export function EventTimeline({ events, hasSelection }: EventTimelineProps) {
  const units = useTimelineGroups(events);

  if (!hasSelection) {
    return (
      <div style={{ color: "var(--color-text-secondary, #9ca3af)", padding: 8 }}>
        Pick a session on the left.
      </div>
    );
  }
  if (units.length === 0) {
    return (
      <div style={{ color: "var(--color-text-secondary, #9ca3af)", padding: 8 }}>
        No events yet.
      </div>
    );
  }
  return (
    <AnimatePresence initial={false}>
      {units.map((u) => {
        if (u.kind === "lifecycle") {
          return <LifecycleMarker key={u.id} event={u.event} />;
        }
        if (u.kind === "assistant") {
          return (
            <AssistantBubble
              key={u.id}
              text={u.text}
              thinking={u.thinking}
              streaming={u.streaming}
              timestamp={u.timestamp}
            />
          );
        }
        if (u.kind === "tool") {
          return <ToolCallCard key={u.id} group={u.group} />;
        }
        // raw: 未识别事件 — 退化到原 JSON 显示，方便排查协议偏移
        return (
          <motion.div
            key={u.id}
            layout="position"
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.16, ease: "easeOut" }}
            style={{
              padding: "6px 8px",
              borderBottom: "1px dashed color-mix(in srgb, var(--midground-base, #ffe6cb) 12%, transparent)",
              fontFamily: "var(--theme-font-mono, monospace)",
              fontSize: 11,
              color: "var(--color-text-secondary, #6b7280)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ fontWeight: 600 }}>{u.event.type}</span>
              <span>{new Date(u.event.created_at).toLocaleTimeString()}</span>
            </div>
            <pre
              style={{
                margin: "4px 0 0",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                color: "var(--midground, #ffe6cb)",
              }}
            >
              {JSON.stringify(u.event.payload, null, 2)}
            </pre>
          </motion.div>
        );
      })}
    </AnimatePresence>
  );
}
