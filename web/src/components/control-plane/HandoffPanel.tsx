/**
 * HandoffPanel — Provider 转交按钮 + 历史。
 *
 * - 当前 session.runtime_kind = claude → 显示「转交给 Codex」「Codex 评审」
 * - 当前 session.runtime_kind = codex  → 显示「转交给 Claude」「Claude 评审」
 *
 * 转交触发 POST，成功后 toast 提示并跳到新 session。历史展示在按钮下方。
 *
 * V1.1 P1 — Provider 转交（Claude ↔ Codex 双向手动转交）
 */
import { useState } from "react";
import { useHandoffs } from "@/hooks/control-plane";
import type { HandoffRecord } from "@/hooks/control-plane";

export interface HandoffPanelProps {
  sessionId: string | null | undefined;
  /** 当前 session 的 runtime_kind（用来决定按钮目标） */
  currentProvider: "claude" | "codex" | null | undefined;
  /** 创建成功后回调，参数为新 session id（页面切到新 session） */
  onHandoffCreated?: (newSessionId: string) => void;
}

const PROVIDER_LABEL: Record<string, string> = {
  claude: "Claude",
  codex: "Codex",
};

export function HandoffPanel({
  sessionId,
  currentProvider,
  onHandoffCreated,
}: HandoffPanelProps) {
  const { handoffs, loading, error, createHandoff } = useHandoffs(sessionId);
  const [busy, setBusy] = useState<null | string>(null);
  const [extraPrompt, setExtraPrompt] = useState("");
  const [expanded, setExpanded] = useState(false);

  if (!sessionId || !currentProvider) return null;
  const target: "claude" | "codex" =
    currentProvider === "claude" ? "codex" : "claude";

  const trigger = async (kind: "transfer" | "review") => {
    if (busy) return;
    setBusy(kind);
    const result = await createHandoff({
      target_provider: target,
      kind,
      include_diff: kind === "review", // review 默认带 diff，transfer 默认不带
      extra_prompt: extraPrompt || undefined,
      context_messages: 6,
    });
    setBusy(null);
    if (result) {
      setExtraPrompt("");
      onHandoffCreated?.(result.to_session_id);
    }
  };

  const baseBtn: React.CSSProperties = {
    fontSize: 12,
    padding: "5px 10px",
    border: "1px solid #3f3f46",
    borderRadius: 4,
    background: "#18181b",
    color: "#e4e4e7",
    cursor: busy ? "wait" : "pointer",
    opacity: busy ? 0.6 : 1,
  };

  return (
    <div
      style={{
        padding: "8px 10px",
        borderTop: "1px solid #27272a",
        display: "flex",
        flexDirection: "column",
        gap: 6,
        fontSize: 12,
      }}
    >
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ color: "#a1a1aa" }}>转交至 {PROVIDER_LABEL[target]}:</span>
        <button
          style={baseBtn}
          disabled={!!busy}
          onClick={() => trigger("transfer")}
          title="把上下文转交给目标 provider 继续干"
        >
          {busy === "transfer" ? "转交中…" : "→ 接力"}
        </button>
        <button
          style={baseBtn}
          disabled={!!busy}
          onClick={() => trigger("review")}
          title="把当前 diff 给目标 provider 评审"
        >
          {busy === "review" ? "请求中…" : "↻ 评审"}
        </button>
        <button
          onClick={() => setExpanded((e) => !e)}
          style={{
            ...baseBtn,
            padding: "5px 8px",
            background: "transparent",
            color: "#71717a",
          }}
        >
          {expanded ? "▼" : "▶"} 历史 ({handoffs.length})
        </button>
      </div>

      <input
        type="text"
        placeholder="（可选）附加说明"
        value={extraPrompt}
        onChange={(e) => setExtraPrompt(e.target.value)}
        style={{
          padding: "4px 6px",
          background: "#09090b",
          border: "1px solid #3f3f46",
          borderRadius: 4,
          color: "#e4e4e7",
          fontSize: 12,
        }}
      />

      {error && <span style={{ color: "#ef4444", fontSize: 11 }}>转交失败: {error}</span>}

      {expanded && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {loading && <span style={{ color: "#71717a", fontSize: 11 }}>加载中…</span>}
          {!loading && handoffs.length === 0 && (
            <span style={{ color: "#71717a", fontSize: 11 }}>暂无转交记录</span>
          )}
          {handoffs.map((h: HandoffRecord) => (
            <div
              key={h.id}
              style={{
                padding: "4px 6px",
                background: "#09090b",
                borderLeft: `3px solid ${h.status === "ok" ? "#22c55e" : "#ef4444"}`,
                fontSize: 11,
                color: "#a1a1aa",
              }}
            >
              <div>
                <strong style={{ color: "#e4e4e7" }}>
                  {PROVIDER_LABEL[h.from_provider]} → {PROVIDER_LABEL[h.to_provider]}
                </strong>{" "}
                · {h.kind === "transfer" ? "接力" : "评审"} ·{" "}
                <span style={{ color: h.status === "ok" ? "#22c55e" : "#ef4444" }}>
                  {h.status}
                </span>
              </div>
              <div style={{ color: "#71717a", fontSize: 10 }}>
                {h.created_at} → {h.to_session_id.slice(0, 12)}…
              </div>
              {h.error && (
                <div style={{ color: "#ef4444", fontSize: 10 }}>{h.error}</div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
