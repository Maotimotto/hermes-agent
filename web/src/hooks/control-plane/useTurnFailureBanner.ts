/**
 * useTurnFailureBanner — 把最新一次未消化的 turn.failed 事件
 * 提取成 ErrorBanner 可消费的状态（V1.1 错误恢复 Wave D）。
 *
 * 行为：
 *   - 输入 events 流；找到最后一条 turn.failed
 *   - 若该事件 id 已被 dismiss → 不返回 banner
 *   - 出现新的 turn.completed / turn.cancelled / turn.started（同一 session
 *     的更新生命周期）→ 自动认定旧的 turn.failed 已不再相关，banner 消失
 *
 * 也接受外部触发的「API 错误」（如 sendTurn 拒绝 503）；这部分独立维护，
 * 与事件态互不覆盖（API 错误优先级更高，因为是用户主动操作的直接反馈）。
 */

import { useMemo, useState } from "react";
import type { EventRecord } from "@/pages/control-plane/types";

export type TurnFailureBannerEvent = {
  /** 事件 id（用于 dismiss 状态去重） */
  eventId: number;
  title: string;
  message: string;
  code?: string;
  retryable?: boolean;
};

export type TurnFailureBannerApi = {
  title: string;
  message: string;
  code?: string;
  hint?: string;
};

export type UseTurnFailureBannerResult = {
  /** 来自 turn.failed 事件的 banner，已应用 dismiss 过滤。 */
  eventBanner: TurnFailureBannerEvent | null;
  /** 来自 API 错误的 banner（如 503 provider_unavailable）。 */
  apiBanner: TurnFailureBannerApi | null;
  /** 关闭事件 banner（标记 eventId 已 dismiss）。 */
  dismissEventBanner: () => void;
  /** 设置 / 清除 API banner。setApiBanner(null) 清除。 */
  setApiBanner: (b: TurnFailureBannerApi | null) => void;
};

function asString(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function asBool(v: unknown): boolean | undefined {
  return typeof v === "boolean" ? v : undefined;
}

export function useTurnFailureBanner(
  events: EventRecord[],
): UseTurnFailureBannerResult {
  const [dismissedId, setDismissedId] = useState<number | null>(null);
  const [apiBanner, setApiBanner] = useState<TurnFailureBannerApi | null>(null);

  // 找最近一次 turn.failed；如果之后又有新的 turn 生命周期，banner 也消失。
  const latestFailed = useMemo(() => {
    let lastFailed: EventRecord | null = null;
    let supersededBy: EventRecord | null = null;
    for (const e of events) {
      if (e.type === "turn.failed") {
        lastFailed = e;
        supersededBy = null;
      } else if (
        lastFailed !== null &&
        (e.type === "turn.completed" ||
          e.type === "turn.cancelled" ||
          e.type === "turn.started")
      ) {
        supersededBy = e;
      }
    }
    if (supersededBy !== null) return null;
    return lastFailed;
  }, [events]);

  // 注意：不需要 effect 把 dismissedId 重置。下面 useMemo 比较 latestFailed.id
  // 与 dismissedId 当不再匹配时自然就让新 banner 显示出来，旧的 dismissed
  // 值留着也不影响（next dismiss 操作会覆盖）。

  const eventBanner: TurnFailureBannerEvent | null = useMemo(() => {
    if (!latestFailed) return null;
    if (dismissedId === latestFailed.id) return null;
    const error = asString(latestFailed.payload?.error) || "Turn failed.";
    const code = asString(latestFailed.payload?.code) || undefined;
    const retryable = asBool(latestFailed.payload?.retryable);
    return {
      eventId: latestFailed.id,
      title: "Turn failed",
      message: error,
      code,
      retryable,
    };
  }, [latestFailed, dismissedId]);

  return {
    eventBanner,
    apiBanner,
    dismissEventBanner: () => {
      if (latestFailed) setDismissedId(latestFailed.id);
    },
    setApiBanner,
  };
}
