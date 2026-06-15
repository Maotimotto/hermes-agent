/**
 * EventTimeline — 选中 session 的事件流渲染。
 *
 * 纯展示组件：events 由 useEventStream 提供，按 id 升序，已 dedupe。
 * 空态根据上下文显示「未选 session」/「无事件」/「加载中」三种态。
 */

import { motion } from "motion/react";
import type { EventRecord } from "@/pages/control-plane/types";

export type EventTimelineProps = {
  events: EventRecord[];
  hasSelection: boolean;
};

export function EventTimeline({ events, hasSelection }: EventTimelineProps) {
  if (!hasSelection) {
    return (
      <div style={{ color: "#9ca3af", padding: 8 }}>
        Pick a session on the left.
      </div>
    );
  }
  if (events.length === 0) {
    return <div style={{ color: "#9ca3af", padding: 8 }}>No events yet.</div>;
  }
  return (
    <>
      {events.map((ev) => (
        <motion.div
          key={ev.id}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.16, ease: "easeOut" }}
          style={{
            padding: "6px 8px",
            borderBottom: "1px solid #f3f4f6",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span style={{ color: "#3b82f6", fontWeight: 600 }}>{ev.type}</span>
            <span style={{ color: "#9ca3af" }}>
              {new Date(ev.created_at).toLocaleTimeString()}
            </span>
          </div>
          <pre
            style={{
              margin: "4px 0 0",
              fontSize: 11,
              color: "#374151",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {JSON.stringify(ev.payload, null, 2)}
          </pre>
        </motion.div>
      ))}
    </>
  );
}
