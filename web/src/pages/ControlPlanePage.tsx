/**
 * ControlPlanePage — 本地 agent 控制台主入口（V1.0.0 Wave 10）
 *
 * 三栏布局：
 *  - 左：sessions 列表 + 创建表单
 *  - 中：选中 session 的事件流 + WS 状态 badge + 发 turn 输入框
 *  - 右：pending approvals + 决策按钮
 *
 * 数据全部来自 hooks/control-plane（useSessions / useEventStream / useApprovals）。
 * 组件来自 components/control-plane。页面本身只做布局 + 状态聚合。
 */

import { useState } from "react";
import { fetchJSON } from "@/lib/api";
import { useSessions, useEventStream, useApprovals } from "@/hooks/control-plane";
import {
  SessionCard,
  CreateSessionForm,
  EventTimeline,
  ChatComposer,
  ApprovalCard,
  WsStatusBadge,
} from "@/components/control-plane";

export default function ControlPlanePage() {
  const {
    sessions,
    selectedSid,
    setSelectedSid,
    loading,
    error: sessionsError,
    refresh: refreshSessions,
    createSession,
    deleteSession,
  } = useSessions();

  const {
    events,
    wsStatus,
    error: eventsError,
  } = useEventStream(selectedSid);

  const {
    approvals,
    error: approvalsError,
    decide,
  } = useApprovals();

  // 创建表单可见性
  const [showCreate, setShowCreate] = useState(false);

  // 发 turn 表单
  const [prompt, setPrompt] = useState("");
  const [sending, setSending] = useState(false);

  const sendTurn = async () => {
    if (!selectedSid || !prompt.trim()) return;
    setSending(true);
    try {
      await fetchJSON(`/control-plane/sessions/${selectedSid}/turns`, {
        method: "POST",
        body: JSON.stringify({ prompt: prompt.trim() }),
        headers: { "Content-Type": "application/json" },
      });
      setPrompt("");
    } catch {
      // error already surfaced via eventsError if needed
    } finally {
      setSending(false);
    }
  };

  const handleDelete = (sid: string) => {
    if (!confirm(`Delete session ${sid.slice(0, 16)}?`)) return;
    deleteSession(sid);
  };

  const error = sessionsError || eventsError || approvalsError;

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
          <CreateSessionForm
            onSubmit={async (input) => {
              const result = await createSession(input);
              if (result) setShowCreate(false);
            }}
            onCancel={() => setShowCreate(false)}
          />
        )}

        <div style={{ flex: 1, overflow: "auto" }}>
          {sessions.length === 0 && (
            <div style={{ padding: 12, color: "#9ca3af", fontSize: 13 }}>
              {loading ? "Loading…" : "No sessions yet."}
            </div>
          )}
          {sessions.map((s) => (
            <SessionCard
              key={s.id}
              session={s}
              selected={selectedSid === s.id}
              onSelect={setSelectedSid}
              onDelete={handleDelete}
            />
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
        <header style={{ padding: "10px 12px", borderBottom: "1px solid #e5e7eb" }}>
          <strong>Events</strong>
          {selectedSid && (
            <span
              style={{
                marginLeft: 8,
                fontFamily: "monospace",
                fontSize: 12,
                color: "#6b7280",
              }}
            >
              {selectedSid}
            </span>
          )}
          {selectedSid && <WsStatusBadge status={wsStatus} />}
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
          <EventTimeline events={events} hasSelection={!!selectedSid} />
        </div>

        {selectedSid && (
          <ChatComposer
            value={prompt}
            onChange={setPrompt}
            onSend={sendTurn}
            sending={sending}
          />
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
        <header style={{ padding: "10px 12px", borderBottom: "1px solid #e5e7eb" }}>
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
            <ApprovalCard key={a.id} approval={a} onDecide={decide} />
          ))}
        </div>
      </section>

      {/* ── 全局错误 toast ──────────────────────── */}
      {error && (
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
          {error}
        </div>
      )}
    </div>
  );
}
