/**
 * ControlPlanePage — 本地 agent 控制台主入口（V1.0.0 Wave 10）
 *
 * 三个面板：
 *  - 左：sessions 列表（GET /control-plane/sessions）
 *  - 中：选中 session 的事件流（GET /control-plane/sessions/:id/events）
 *  - 右：pending approvals + 决策按钮（GET /control-plane/approvals）
 *
 * 这一版只做读 + 决策；新建 session / 发 turn 留给 Wave 10.2 / 10.3。
 */

import { useEffect, useState, useCallback } from "react";
import { fetchJSON } from "@/lib/api";

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
      setEvents(data.events);
    } catch (e) {
      setErr(`Failed to load events: ${(e as Error).message}`);
    }
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

  useEffect(() => {
    refreshSessions();
    refreshApprovals();
    const t = setInterval(refreshApprovals, 5000);
    return () => clearInterval(t);
  }, [refreshSessions, refreshApprovals]);

  useEffect(() => {
    if (selectedSid) {
      refreshEvents(selectedSid);
      const t = setInterval(() => refreshEvents(selectedSid), 3000);
      return () => clearInterval(t);
    }
  }, [selectedSid, refreshEvents]);

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
          }}
        >
          <strong>Sessions</strong>
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
            {loading ? "..." : "Refresh"}
          </button>
        </header>
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
