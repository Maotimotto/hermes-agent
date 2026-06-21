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

import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AnimatePresence } from "motion/react";
import { useSessions, useEventStream, useApprovals, useTurnFailureBanner } from "@/hooks/control-plane";
import {
  SessionCard,
  CreateSessionForm,
  EventTimeline,
  ChatComposer,
  ApprovalCard,
  WsStatusBadge,
  ErrorToast,
  ErrorBanner,
  FileChangePanel,
  DiffPanel,
  ControlPlaneTopBar,
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

  // Allow deep-linking from /history → /control-plane?session=<sid>
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    const sid = searchParams.get("session");
    if (sid && sid !== selectedSid) {
      setSelectedSid(sid);
      // Strip the query so a subsequent manual selection isn't overridden.
      const next = new URLSearchParams(searchParams);
      next.delete("session");
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

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

  // 错误恢复 banner（事件态 turn.failed + API 态 503 等）
  const {
    eventBanner,
    apiBanner,
    dismissEventBanner,
    setApiBanner,
  } = useTurnFailureBanner(events);

  const sendTurn = async () => {
    if (!selectedSid || !prompt.trim()) return;
    setSending(true);
    try {
      const res = await fetch(`/control-plane/sessions/${selectedSid}/turns`, {
        method: "POST",
        body: JSON.stringify({ prompt: prompt.trim() }),
        headers: { "Content-Type": "application/json" },
      });
      if (!res.ok) {
        // 拉错误 detail；503 provider_unavailable 走专用文案
        const text = await res.text();
        let detail: Record<string, unknown> | null = null;
        try {
          const parsed: unknown = JSON.parse(text);
          if (parsed && typeof parsed === "object") {
            detail = parsed as Record<string, unknown>;
          }
        } catch {
          /* ignore */
        }
        const innerCandidate =
          (detail?.detail && typeof detail.detail === "object"
            ? (detail.detail as Record<string, unknown>)
            : null) ??
          (detail?.error &&
          typeof detail.error === "object" &&
          (detail.error as Record<string, unknown>).detail &&
          typeof (detail.error as Record<string, unknown>).detail === "object"
            ? ((detail.error as Record<string, unknown>).detail as Record<string, unknown>)
            : null);
        const innerCode =
          innerCandidate && typeof innerCandidate.code === "string"
            ? innerCandidate.code
            : null;
        const innerKind =
          innerCandidate && typeof innerCandidate.kind === "string"
            ? innerCandidate.kind
            : null;
        const innerMessage =
          innerCandidate && typeof innerCandidate.message === "string"
            ? innerCandidate.message
            : null;
        const innerHint =
          innerCandidate && typeof innerCandidate.hint === "string"
            ? innerCandidate.hint
            : null;
        if (res.status === 503 && innerCode === "provider_unavailable") {
          setApiBanner({
            title: "Provider unavailable",
            code: innerKind ? `provider:${innerKind}` : "provider_unavailable",
            message: innerMessage || "Provider is currently unavailable.",
            hint: innerHint || undefined,
          });
        } else {
          setApiBanner({
            title: `Failed to start turn (${res.status})`,
            message: text.slice(0, 500) || "Unknown error.",
          });
        }
        return;
      }
      setPrompt("");
      setApiBanner(null);
    } catch (e) {
      setApiBanner({
        title: "Network error",
        message: (e as Error).message || "Could not reach the daemon.",
      });
    } finally {
      setSending(false);
    }
  };

  const retryFromBanner = () => {
    // 重试就是把上一条 prompt 再发一次。空 prompt 时不动作。
    if (!prompt.trim()) return;
    void sendTurn();
  };

  const handleDelete = (sid: string) => {
    if (!confirm(`Delete session ${sid.slice(0, 16)}?`)) return;
    deleteSession(sid);
  };

  const error = sessionsError || eventsError || approvalsError;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "calc(100vh - 60px)",
        boxSizing: "border-box",
      }}
    >
      <ControlPlaneTopBar />
      <AnimatePresence initial={false}>
        {apiBanner && (
          <ErrorBanner
            key={`api-banner`}
            source="api"
            title={apiBanner.title}
            code={apiBanner.code}
            message={apiBanner.message}
            hint={apiBanner.hint}
            onRetry={prompt.trim() ? retryFromBanner : undefined}
            onDismiss={() => setApiBanner(null)}
          />
        )}
        {!apiBanner && eventBanner && (
          <ErrorBanner
            key={`evt-${eventBanner.eventId}`}
            source="event"
            title={eventBanner.title}
            code={eventBanner.code}
            message={eventBanner.message}
            retryable={eventBanner.retryable}
            onDismiss={dismissEventBanner}
          />
        )}
      </AnimatePresence>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "320px 1fr 360px",
          gap: 12,
          flex: 1,
          minHeight: 0,
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
          <AnimatePresence initial={false}>
            {sessions.map((s) => (
              <SessionCard
                key={s.id}
                session={s}
                selected={selectedSid === s.id}
                onSelect={setSelectedSid}
                onDelete={handleDelete}
              />
            ))}
          </AnimatePresence>
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
          {selectedSid && <FileChangePanel events={events} />}
          {selectedSid && (
            <DiffPanel
              workspaceId={sessions.find((s) => s.id === selectedSid)?.workspace_id ?? null}
            />
          )}
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
          <AnimatePresence initial={false}>
            {approvals.map((a) => (
              <ApprovalCard key={a.id} approval={a} onDecide={decide} />
            ))}
          </AnimatePresence>
        </div>
      </section>
      </div>

      {/* ── 全局错误 toast ──────────────────────── */}
      <AnimatePresence>
        {error && (
          <ErrorToast key="err" message={error} />
        )}
      </AnimatePresence>
    </div>
  );
}
