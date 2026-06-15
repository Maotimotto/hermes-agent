/**
 * ControlPlanePage — 本地 agent 控制台主入口（V1.0.0 Wave 10）
 *
 * 三个面板：
 *  - 左：sessions 列表（GET /control-plane/sessions）
 *  - 中：选中 session 的事件流（GET /control-plane/sessions/:id/events，
 *        实时更新走 WebSocket /control-plane/sessions/:id/events/ws）
 *  - 右：pending approvals + 决策按钮（GET /control-plane/approvals）
 *
 * 实时层（2026-06-15 加）：
 *  - 选中 session 后先 HTTP 拉一次历史事件做 backfill
 *  - 然后开 WebSocket 订阅 server push（heartbeat 1Hz）
 *  - 收到非 heartbeat 消息时增量 append（按 id dedupe）
 *  - WS 断开 / 报错时降级到 3s polling，避免完全失联
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { fetchJSON, HERMES_BASE_PATH } from "@/lib/api";

type SessionRecord = {
  id: string;
  runtime_kind: string;
  model: string;
  repo_path?: string | null;
  workspace_id?: string | null;
  status: string;
  started_at: string;
  ended_at?: string | null;
  metadata?: Record<string, unknown>;
};

type SessionListResponse = {
  sessions: SessionRecord[];
  total: number;
  limit: number;
  offset: number;
};

type EventRecord = {
  id: number;
  session_id: string;
  turn_id?: string | null;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
};

type EventsResponse = {
  events: EventRecord[];
};

type ApprovalRecord = {
  id: string;
  session_id: string;
  action_kind: string;
  action_payload: Record<string, unknown>;
  decision: "approved" | "denied" | "pending";
  decided_by?: string | null;
  decided_at?: string | null;
  created_at?: string;
};

type ApprovalsResponse = ApprovalRecord[];

const STATUS_COLORS: Record<string, string> = {
  created: "#6b7280",
  running: "#10b981",
  waiting: "#f59e0b",
  completed: "#3b82f6",
  failed: "#ef4444",
  cancelled: "#9ca3af",
  stopped: "#9ca3af",
};

export default function ControlPlanePage() {
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [selectedSid, setSelectedSid] = useState<string | null>(null);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 创建 session 表单
  const [showCreate, setShowCreate] = useState(false);
  const [newRuntime, setNewRuntime] = useState<"claude" | "codex">("claude");
  const [newModel, setNewModel] = useState("claude-opus-4-7");
  const [newRepo, setNewRepo] = useState("");

  // 发 turn 表单
  const [prompt, setPrompt] = useState("");
  const [sending, setSending] = useState(false);

  // WS / fallback polling 状态
  const [wsStatus, setWsStatus] = useState<
    "idle" | "connecting" | "open" | "polling" | "closed"
  >("idle");
  const wsRef = useRef<WebSocket | null>(null);

  const refreshSessions = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const data = await fetchJSON<SessionListResponse>(
        "/control-plane/sessions?limit=50",
      );
      setSessions(data.sessions);
      if (!selectedSid && data.sessions.length > 0) {
        setSelectedSid(data.sessions[0].id);
      }
    } catch (e) {
      setErr(`Failed to load sessions: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [selectedSid]);

  const refreshEvents = useCallback(async (sid: string) => {
    try {
      const data = await fetchJSON<EventsResponse>(
        `/control-plane/sessions/${sid}/events?limit=200`,
      );
      // 直接覆盖（HTTP 拉的是权威全量，WS 后续增量 append）
      setEvents(data.events);
    } catch (e) {
      setErr(`Failed to load events: ${(e as Error).message}`);
    }
  }, []);

  // 增量 append 单个 event（WS push 用）；按 id dedupe + 保持升序
  const appendEvent = useCallback((evt: EventRecord) => {
    setEvents((prev) => {
      // dedupe by id
      if (prev.some((e) => e.id === evt.id)) return prev;
      // 大多数情况下 evt.id > 末尾.id，直接 push 即可
      const last = prev[prev.length - 1];
      if (!last || evt.id > last.id) return [...prev, evt];
      // 否则按 id 排序插入（保护乱序投递场景）
      const next = [...prev, evt];
      next.sort((a, b) => a.id - b.id);
      return next;
    });
  }, []);

  const refreshApprovals = useCallback(async () => {
    try {
      const data = await fetchJSON<ApprovalsResponse>(
        "/control-plane/approvals?status=pending",
      );
      setApprovals(data);
    } catch (e) {
      // approvals route 可能还没 list 接口；静默忽略
      setApprovals([]);
      void e;
    }
  }, []);

  const createSession = async () => {
    setErr(null);
    try {
      const body: Record<string, unknown> = {
        runtime_kind: newRuntime,
        model: newModel,
      };
      if (newRepo.trim()) body.repo_path = newRepo.trim();
      const data = await fetchJSON<SessionRecord>(
        "/control-plane/sessions",
        {
          method: "POST",
          body: JSON.stringify(body),
          headers: { "Content-Type": "application/json" },
        },
      );
      setShowCreate(false);
      await refreshSessions();
      setSelectedSid(data.id);
    } catch (e) {
      setErr(`Create session failed: ${(e as Error).message}`);
    }
  };

  const sendTurn = async () => {
    if (!selectedSid || !prompt.trim()) return;
    setSending(true);
    setErr(null);
    try {
      await fetchJSON(
        `/control-plane/sessions/${selectedSid}/turns`,
        {
          method: "POST",
          body: JSON.stringify({ prompt: prompt.trim() }),
          headers: { "Content-Type": "application/json" },
        },
      );
      setPrompt("");
      await refreshEvents(selectedSid);
    } catch (e) {
      setErr(`Send turn failed: ${(e as Error).message}`);
    } finally {
      setSending(false);
    }
  };

  const deleteSession = async (sid: string) => {
    if (!confirm(`Delete session ${sid.slice(0, 16)}?`)) return;
    try {
      await fetchJSON(`/control-plane/sessions/${sid}`, { method: "DELETE" });
      if (selectedSid === sid) setSelectedSid(null);
      await refreshSessions();
    } catch (e) {
      setErr(`Delete failed: ${(e as Error).message}`);
    }
  };

  useEffect(() => {
    refreshSessions();
    refreshApprovals();
    const t = setInterval(refreshApprovals, 5000);
    return () => clearInterval(t);
  }, [refreshSessions, refreshApprovals]);

  useEffect(() => {
    if (!selectedSid) {
      setWsStatus("idle");
      return;
    }
    const sid = selectedSid;

    // 1) 先做一次 backfill（清空旧 session 的 events）
    setEvents([]);
    refreshEvents(sid);

    // 2) 开 WebSocket 实时订阅
    let cancelled = false;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let ws: WebSocket | null = null;

    const startPollingFallback = () => {
      if (cancelled || pollTimer) return;
      setWsStatus("polling");
      pollTimer = setInterval(() => {
        if (cancelled) return;
        refreshEvents(sid);
      }, 3000);
    };

    try {
      // ws://host[/base-path]/control-plane/sessions/<sid>/events/ws
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const base = HERMES_BASE_PATH || "";
      const url = `${proto}//${window.location.host}${base}/control-plane/sessions/${sid}/events/ws`;
      setWsStatus("connecting");
      ws = new WebSocket(url);
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
        if (cancelled) return;
        // 不立即降级，等 onclose 统一处理
      };

      ws.onclose = () => {
        if (cancelled) return;
        setWsStatus("closed");
        // WS 断了：起 polling 兜底，避免界面停滞
        startPollingFallback();
      };
    } catch {
      // 浏览器不支持 / URL 构造失败 → 直接走 polling
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
  }, [selectedSid, refreshEvents, appendEvent]);

  const decideApproval = async (
    approvalId: string,
    decision: "approved" | "denied",
  ) => {
    try {
      await fetchJSON(`/control-plane/approvals/${approvalId}/decision`, {
        method: "POST",
        body: JSON.stringify({ decision, decided_by: "user" }),
        headers: { "Content-Type": "application/json" },
      });
      refreshApprovals();
    } catch (e) {
      setErr(`Approval decision failed: ${(e as Error).message}`);
    }
  };

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "320px 1fr 360px",
        gap: 12,
        height: "calc(100vh - 60px)",
        padding: 12,
        boxSizing: "border-box",
      }}
    >
      {/* ── 左：sessions ───────────────────────── */}
      <section
        style={{
          border: "1px solid #e5e7eb",
          borderRadius: 8,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <header
          style={{
            padding: "10px 12px",
            borderBottom: "1px solid #e5e7eb",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 6,
          }}
        >
          <strong>Sessions</strong>
          <div style={{ display: "flex", gap: 6 }}>
            <button
              onClick={() => setShowCreate(true)}
              style={{
                fontSize: 12,
                padding: "4px 8px",
                border: "1px solid #3b82f6",
                borderRadius: 4,
                background: "#3b82f6",
                color: "white",
                cursor: "pointer",
              }}
            >
              + New
            </button>
            <button
              onClick={refreshSessions}
              disabled={loading}
              style={{
                fontSize: 12,
                padding: "4px 8px",
                border: "1px solid #d1d5db",
                borderRadius: 4,
                background: "white",
                cursor: "pointer",
              }}
            >
              {loading ? "..." : "↻"}
            </button>
          </div>
        </header>
        {showCreate && (
          <div
            style={{
              padding: 12,
              background: "#f9fafb",
              borderBottom: "1px solid #e5e7eb",
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}
          >
            <label style={{ fontSize: 12, color: "#374151" }}>
              Runtime
              <select
                value={newRuntime}
                onChange={(e) =>
                  setNewRuntime(e.target.value as "claude" | "codex")
                }
                style={{
                  display: "block",
                  width: "100%",
                  marginTop: 4,
                  padding: "4px 6px",
                  fontSize: 12,
                  border: "1px solid #d1d5db",
                  borderRadius: 4,
                }}
              >
                <option value="claude">claude</option>
                <option value="codex">codex</option>
              </select>
            </label>
            <label style={{ fontSize: 12, color: "#374151" }}>
              Model
              <input
                value={newModel}
                onChange={(e) => setNewModel(e.target.value)}
                style={{
                  display: "block",
                  width: "100%",
                  marginTop: 4,
                  padding: "4px 6px",
                  fontSize: 12,
                  border: "1px solid #d1d5db",
                  borderRadius: 4,
                  boxSizing: "border-box",
                }}
              />
            </label>
            <label style={{ fontSize: 12, color: "#374151" }}>
              Repo path (optional)
              <input
                value={newRepo}
                onChange={(e) => setNewRepo(e.target.value)}
                placeholder="/home/.../project"
                style={{
                  display: "block",
                  width: "100%",
                  marginTop: 4,
                  padding: "4px 6px",
                  fontSize: 12,
                  border: "1px solid #d1d5db",
                  borderRadius: 4,
                  boxSizing: "border-box",
                }}
              />
            </label>
            <div style={{ display: "flex", gap: 6 }}>
              <button
                onClick={createSession}
                style={{
                  flex: 1,
                  padding: "6px 10px",
                  background: "#10b981",
                  color: "white",
                  border: "none",
                  borderRadius: 4,
                  cursor: "pointer",
                  fontSize: 12,
                }}
              >
                Create
              </button>
              <button
                onClick={() => setShowCreate(false)}
                style={{
                  flex: 1,
                  padding: "6px 10px",
                  background: "white",
                  color: "#374151",
                  border: "1px solid #d1d5db",
                  borderRadius: 4,
                  cursor: "pointer",
                  fontSize: 12,
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
        <div style={{ flex: 1, overflow: "auto" }}>
          {sessions.length === 0 && (
            <div style={{ padding: 12, color: "#9ca3af", fontSize: 13 }}>
              {loading ? "Loading…" : "No sessions yet."}
            </div>
          )}
          {sessions.map((s) => (
            <div
              key={s.id}
              onClick={() => setSelectedSid(s.id)}
              style={{
                padding: "10px 12px",
                borderBottom: "1px solid #f3f4f6",
                cursor: "pointer",
                background: selectedSid === s.id ? "#eff6ff" : "white",
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
                <span>{s.id.slice(0, 16)}</span>
                <span
                  style={{
                    color: STATUS_COLORS[s.status] || "#6b7280",
                    fontWeight: 600,
                  }}
                >
                  {s.status}
                </span>
              </div>
              <div style={{ fontSize: 12, color: "#6b7280", marginTop: 4 }}>
                {s.runtime_kind} · {s.model}
              </div>
              {s.repo_path && (
                <div
                  style={{
                    fontSize: 11,
                    color: "#9ca3af",
                    marginTop: 2,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={s.repo_path}
                >
                  📁 {s.repo_path}
                </div>
              )}
              {selectedSid === s.id && s.status !== "stopped" && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteSession(s.id);
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
            </div>
          ))}
        </div>
      </section>

      {/* ── 中：events ─────────────────────────── */}
      <section
        style={{
          border: "1px solid #e5e7eb",
          borderRadius: 8,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <header
          style={{
            padding: "10px 12px",
            borderBottom: "1px solid #e5e7eb",
          }}
        >
          <strong>Events</strong>
          {selectedSid && (
            <span style={{ marginLeft: 8, fontFamily: "monospace", fontSize: 12, color: "#6b7280" }}>
              {selectedSid}
            </span>
          )}
          {selectedSid && (
            <span
              title={
                wsStatus === "open"
                  ? "WebSocket 实时推送中"
                  : wsStatus === "polling"
                    ? "WS 不可用，已降级到 3s 轮询"
                    : wsStatus === "connecting"
                      ? "正在建立 WebSocket 连接"
                      : wsStatus === "closed"
                        ? "WebSocket 已关闭"
                        : "未连接"
              }
              style={{
                marginLeft: 10,
                fontSize: 11,
                padding: "1px 6px",
                borderRadius: 999,
                background:
                  wsStatus === "open"
                    ? "#10b98120"
                    : wsStatus === "polling"
                      ? "#f59e0b20"
                      : wsStatus === "connecting"
                        ? "#3b82f620"
                        : "#9ca3af20",
                color:
                  wsStatus === "open"
                    ? "#10b981"
                    : wsStatus === "polling"
                      ? "#f59e0b"
                      : wsStatus === "connecting"
                        ? "#3b82f6"
                        : "#6b7280",
              }}
            >
              {wsStatus === "open"
                ? "● live"
                : wsStatus === "polling"
                  ? "◐ poll 3s"
                  : wsStatus === "connecting"
                    ? "○ connecting"
                    : wsStatus === "closed"
                      ? "× closed"
                      : "idle"}
            </span>
          )}
        </header>
        <div
          style={{
            flex: 1,
            overflow: "auto",
            padding: 8,
            fontFamily: "monospace",
            fontSize: 12,
          }}
        >
          {!selectedSid && (
            <div style={{ color: "#9ca3af", padding: 8 }}>
              Pick a session on the left.
            </div>
          )}
          {selectedSid && events.length === 0 && (
            <div style={{ color: "#9ca3af", padding: 8 }}>No events yet.</div>
          )}
          {events.map((ev) => (
            <div
              key={ev.id}
              style={{
                padding: "6px 8px",
                borderBottom: "1px solid #f3f4f6",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                }}
              >
                <span style={{ color: "#3b82f6", fontWeight: 600 }}>
                  {ev.type}
                </span>
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
            </div>
          ))}
        </div>
        {selectedSid && (
          <div
            style={{
              padding: 10,
              borderTop: "1px solid #e5e7eb",
              background: "#f9fafb",
              display: "flex",
              gap: 8,
            }}
          >
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault();
                  sendTurn();
                }
              }}
              placeholder="Send a turn to this session… (Ctrl/Cmd+Enter)"
              rows={2}
              style={{
                flex: 1,
                padding: 8,
                fontSize: 13,
                border: "1px solid #d1d5db",
                borderRadius: 4,
                resize: "vertical",
                fontFamily: "inherit",
                boxSizing: "border-box",
              }}
            />
            <button
              onClick={sendTurn}
              disabled={sending || !prompt.trim()}
              style={{
                padding: "0 14px",
                background:
                  sending || !prompt.trim() ? "#9ca3af" : "#3b82f6",
                color: "white",
                border: "none",
                borderRadius: 4,
                cursor:
                  sending || !prompt.trim() ? "not-allowed" : "pointer",
                fontSize: 13,
                fontWeight: 600,
                whiteSpace: "nowrap",
              }}
            >
              {sending ? "Sending…" : "Send"}
            </button>
          </div>
        )}
      </section>

      {/* ── 右：approvals ──────────────────────── */}
      <section
        style={{
          border: "1px solid #e5e7eb",
          borderRadius: 8,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <header
          style={{
            padding: "10px 12px",
            borderBottom: "1px solid #e5e7eb",
          }}
        >
          <strong>Pending Approvals</strong>
          <span style={{ marginLeft: 8, color: "#6b7280", fontSize: 12 }}>
            ({approvals.length})
          </span>
        </header>
        <div style={{ flex: 1, overflow: "auto", padding: 8 }}>
          {approvals.length === 0 && (
            <div style={{ color: "#9ca3af", padding: 8, fontSize: 13 }}>
              No pending approvals.
            </div>
          )}
          {approvals.map((a) => (
            <div
              key={a.id}
              style={{
                padding: 10,
                marginBottom: 8,
                border: "1px solid #fbbf24",
                background: "#fef3c7",
                borderRadius: 6,
              }}
            >
              <div style={{ fontSize: 12, fontWeight: 600, color: "#92400e" }}>
                {a.action_kind}
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
                {JSON.stringify(a.action_payload, null, 2)}
              </pre>
              <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                <button
                  onClick={() => decideApproval(a.id, "approved")}
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
                  onClick={() => decideApproval(a.id, "denied")}
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
            </div>
          ))}
        </div>
      </section>

      {err && (
        <div
          style={{
            position: "fixed",
            bottom: 16,
            right: 16,
            background: "#fef2f2",
            border: "1px solid #fca5a5",
            color: "#991b1b",
            padding: "8px 12px",
            borderRadius: 6,
            fontSize: 13,
            maxWidth: 400,
          }}
        >
          {err}
        </div>
      )}
    </div>
  );
}
