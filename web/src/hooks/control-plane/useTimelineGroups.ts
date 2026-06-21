/**
 * useTimelineGroups — 把扁平 EventRecord[] 聚合为有序的渲染单元。
 *
 * 聚合规则（路书 10 §6 事件渲染策略）：
 *   - assistant.delta 累积到同一 turn 的 buffer，渲染为一个 AssistantBubble
 *     （turn.completed/failed/cancelled 出现时关闭 streaming）。
 *   - assistant.message 直接产出 AssistantBubble（覆盖之前的累积 buffer）。
 *   - assistant.thinking 累积到当前 turn 的 thinking 文本（与 text 一起挂到
 *     同一气泡上）。
 *   - tool.started 创建 ToolGroup；tool.output 追加 output；tool.completed
 *     更新 status / duration / error。
 *   - file.changed 不进入主时间轴 — 由 FileChangePanel 单独消费 events 数组。
 *   - approval.requested / approval.resolved 不在这里渲染（右侧 Pending
 *     Approvals 列表 + LifecycleMarker fallback 处理）。
 *   - 其它生命周期事件（session.started, turn.*）走 LifecycleMarker。
 */

import { useMemo } from "react";
import type { EventRecord } from "@/pages/control-plane/types";
import type { ToolGroup, ToolStatus } from "@/components/control-plane/ToolCallCard";

export type AssistantUnit = {
  kind: "assistant";
  id: string;
  text: string;
  thinking?: string;
  streaming: boolean;
  timestamp: string;
};

export type ToolUnit = {
  kind: "tool";
  id: string;
  group: ToolGroup;
};

export type LifecycleUnit = {
  kind: "lifecycle";
  id: string;
  event: EventRecord;
};

export type RawUnit = {
  kind: "raw";
  id: string;
  event: EventRecord;
};

export type TimelineUnit = AssistantUnit | ToolUnit | LifecycleUnit | RawUnit;

const LIFECYCLE_TYPES = new Set([
  "session.started",
  "turn.started",
  "turn.completed",
  "turn.failed",
  "turn.cancelled",
  "turn.retrying",
]);

const SUPPRESSED_TYPES = new Set([
  "file.changed", // → FileChangePanel
  "approval.requested", // → 右侧面板 / 单独渲染
  "approval.resolved",
  "question.requested", // V1.0.0 暂不在 timeline 渲染
]);

const TURN_TERMINAL_TYPES = new Set([
  "turn.completed",
  "turn.failed",
  "turn.cancelled",
]);

function asString(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function asNumber(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function useTimelineGroups(events: EventRecord[]): TimelineUnit[] {
  return useMemo(() => buildTimelineUnits(events), [events]);
}

export function buildTimelineUnits(events: EventRecord[]): TimelineUnit[] {
  const units: TimelineUnit[] = [];
  // 当前 turn 的 assistant buffer（按 turn_id 切片，但简单地以最近一条
  // turn.started 为界即可，因为后端按 turn 串行下发事件）。
  type Buffer = { id: string; text: string; thinking: string; lastTs: string; firstEvtId: number };
  let buf: Buffer | null = null;
  // 已写入 units 的 assistant 单元位置（用于追加 streaming 内容时就地更新）
  let bufIndex: number | null = null;
  // 工具卡片索引（toolCallId → units 索引）
  const toolIndex = new Map<string, number>();

  const flushBuffer = (terminal: boolean) => {
    if (!buf || bufIndex === null) {
      buf = null;
      bufIndex = null;
      return;
    }
    const unit = units[bufIndex] as AssistantUnit;
    unit.streaming = !terminal && buf.text.length > 0 && unit.streaming;
    if (terminal) {
      buf = null;
      bufIndex = null;
    }
  };

  const ensureBuffer = (ev: EventRecord): Buffer => {
    if (!buf) {
      buf = {
        id: `asst-${ev.id}`,
        text: "",
        thinking: "",
        lastTs: ev.created_at,
        firstEvtId: ev.id,
      };
      const unit: AssistantUnit = {
        kind: "assistant",
        id: buf.id,
        text: "",
        thinking: undefined,
        streaming: true,
        timestamp: ev.created_at,
      };
      units.push(unit);
      bufIndex = units.length - 1;
    }
    return buf;
  };

  for (const ev of events) {
    if (SUPPRESSED_TYPES.has(ev.type)) continue;

    if (ev.type === "turn.started") {
      // 新 turn 开始 → 关闭旧 buffer（如有）
      flushBuffer(true);
      units.push({ kind: "lifecycle", id: `evt-${ev.id}`, event: ev });
      continue;
    }

    if (TURN_TERMINAL_TYPES.has(ev.type)) {
      flushBuffer(true);
      units.push({ kind: "lifecycle", id: `evt-${ev.id}`, event: ev });
      continue;
    }

    if (LIFECYCLE_TYPES.has(ev.type)) {
      units.push({ kind: "lifecycle", id: `evt-${ev.id}`, event: ev });
      continue;
    }

    if (ev.type === "assistant.delta") {
      const b = ensureBuffer(ev);
      b.text += asString(ev.payload?.text);
      b.lastTs = ev.created_at;
      if (bufIndex !== null) {
        const unit = units[bufIndex] as AssistantUnit;
        unit.text = b.text;
        unit.streaming = true;
        unit.timestamp = b.lastTs;
      }
      continue;
    }

    if (ev.type === "assistant.thinking") {
      const b = ensureBuffer(ev);
      b.thinking += asString(ev.payload?.text);
      b.lastTs = ev.created_at;
      if (bufIndex !== null) {
        const unit = units[bufIndex] as AssistantUnit;
        unit.thinking = b.thinking;
        unit.timestamp = b.lastTs;
      }
      continue;
    }

    if (ev.type === "assistant.message") {
      // 完整消息 — 替换正在流式的 buffer（若没有 buffer 就新建一个最终态）
      const text = asString(ev.payload?.text);
      const localBuf: Buffer = buf ?? ensureBuffer(ev);
      if (bufIndex !== null) {
        const unit = units[bufIndex] as AssistantUnit;
        unit.text = text || localBuf.text;
        unit.thinking = localBuf.thinking || undefined;
        unit.streaming = false;
        unit.timestamp = ev.created_at;
      }
      // 不立即 flush —— 让后续 assistant.delta 仍能挂到这条；但用户视觉上
      // 已经收到完整消息了。turn.* 事件触达时会真正关闭。
      continue;
    }

    if (ev.type === "tool.started") {
      const tcid = asString(ev.payload?.tool_call_id) || `tc_${ev.id}`;
      const group: ToolGroup = {
        id: `tool-${tcid}`,
        toolCallId: tcid,
        toolName: asString(ev.payload?.tool_name) || "tool",
        status: "running",
        input: ev.payload?.input,
        output: "",
        startedAt: ev.created_at,
      };
      units.push({ kind: "tool", id: group.id, group });
      toolIndex.set(tcid, units.length - 1);
      continue;
    }

    if (ev.type === "tool.output") {
      const tcid = asString(ev.payload?.tool_call_id);
      const idx = tcid ? toolIndex.get(tcid) : undefined;
      if (idx !== undefined) {
        const unit = units[idx] as ToolUnit;
        unit.group.output += asString(ev.payload?.text);
      } else {
        // 没找到对应 started — 当原始 raw 处理，不丢
        units.push({ kind: "raw", id: `evt-${ev.id}`, event: ev });
      }
      continue;
    }

    if (ev.type === "tool.completed") {
      const tcid = asString(ev.payload?.tool_call_id);
      const idx = tcid ? toolIndex.get(tcid) : undefined;
      const status = (asString(ev.payload?.status) || "ok") as ToolStatus;
      const duration = asNumber(ev.payload?.duration);
      const error = asString(ev.payload?.error) || null;
      if (idx !== undefined) {
        const unit = units[idx] as ToolUnit;
        unit.group.status = status === "error" ? "error" : "ok";
        unit.group.duration = duration;
        unit.group.error = error;
      } else {
        units.push({ kind: "raw", id: `evt-${ev.id}`, event: ev });
      }
      continue;
    }

    // 兜底：未识别类型 → raw
    units.push({ kind: "raw", id: `evt-${ev.id}`, event: ev });
  }

  // 流尾：如果最后一条事件不是 turn 终态但 buffer 还活着，保留 streaming
  // 状态（让光标继续闪），不强行关闭。
  return units;
}
