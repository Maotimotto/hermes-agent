/**
 * useEventStream — session event stream with WS-first + polling fallback.
 *
 * Lifecycle when `sessionId` changes:
 *   1. Reset events, clear error.
 *   2. HTTP backfill: GET /control-plane/sessions/:id/events?limit=200 (authoritative).
 *   3. Open WS /control-plane/sessions/:id/events/ws and append non-heartbeat
 *      frames in id-sorted, deduped order.
 *   4. On WS close/error, fall back to 3s HTTP polling so the UI never freezes.
 *
 * `wsStatus` reflects current transport so the page can render a live/poll badge.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchJSON, HERMES_BASE_PATH } from "@/lib/api";
import type {
  EventRecord,
  EventsResponse,
  WsStatus,
} from "@/pages/control-plane/types";

export type UseEventStreamResult = {
  events: EventRecord[];
  wsStatus: WsStatus;
  error: string | null;
  refresh: () => Promise<void>;
};

const POLL_FALLBACK_MS = 3000;

function buildWsUrl(sessionId: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const base = HERMES_BASE_PATH || "";
  return `${proto}//${window.location.host}${base}/control-plane/sessions/${sessionId}/events/ws`;
}

export function useEventStream(sessionId: string | null): UseEventStreamResult {
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [wsStatus, setWsStatus] = useState<WsStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    try {
      const data = await fetchJSON<EventsResponse>(
        `/control-plane/sessions/${sessionId}/events?limit=200`,
      );
      setEvents(data.events);
    } catch (e) {
      setError(`Failed to load events: ${(e as Error).message}`);
    }
  }, [sessionId]);

  const appendEvent = useCallback((evt: EventRecord) => {
    setEvents((prev) => {
      if (prev.some((e) => e.id === evt.id)) return prev;
      const last = prev[prev.length - 1];
      if (!last || evt.id > last.id) return [...prev, evt];
      const next = [...prev, evt];
      next.sort((a, b) => a.id - b.id);
      return next;
    });
  }, []);

  useEffect(() => {
    if (!sessionId) {
      setWsStatus("idle");
      setEvents([]);
      return;
    }

    const sid = sessionId;
    let cancelled = false;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let ws: WebSocket | null = null;

    setEvents([]);
    setError(null);
    void refresh();

    const startPollingFallback = () => {
      if (cancelled || pollTimer) return;
      setWsStatus("polling");
      pollTimer = setInterval(() => {
        if (cancelled) return;
        void refresh();
      }, POLL_FALLBACK_MS);
    };

    try {
      setWsStatus("connecting");
      ws = new WebSocket(buildWsUrl(sid));
      wsRef.current = ws;

      ws.onopen = () => {
        if (cancelled) return;
        setWsStatus("open");
      };

      ws.onmessage = (msg: MessageEvent<string>) => {
        if (cancelled) return;
        try {
          const data = JSON.parse(msg.data) as
            | { type: "heartbeat" }
            | EventRecord;
          if ((data as { type?: string }).type === "heartbeat") return;
          appendEvent(data as EventRecord);
        } catch {
          // 损坏帧忽略
        }
      };

      ws.onerror = () => {
        // Wait for onclose to drive fallback so we don't double-start polling.
      };

      ws.onclose = () => {
        if (cancelled) return;
        setWsStatus("closed");
        startPollingFallback();
      };
    } catch {
      startPollingFallback();
    }

    return () => {
      cancelled = true;
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
      if (ws) {
        try {
          ws.close();
        } catch {
          // 忽略关闭异常
        }
        wsRef.current = null;
      }
    };
  }, [sessionId, refresh, appendEvent]);

  return { events, wsStatus, error, refresh };
}
