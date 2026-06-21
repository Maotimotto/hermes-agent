/**
 * ExportButton — Session 日志导出按钮（JSON / Markdown）。
 *
 * V1.1 P1 基础日志导出前端入口。
 * 触发浏览器下载（Content-Disposition: attachment）。
 */
import { useState } from "react";

export type ExportButtonProps = {
  sessionId: string | null | undefined;
};

export function ExportButton({ sessionId }: ExportButtonProps) {
  const [busy, setBusy] = useState<null | "json" | "md">(null);

  const trigger = (ext: "json" | "md") => {
    if (!sessionId || busy) return;
    setBusy(ext);
    // 浏览器 navigate 到 attachment 端点，不会离开页面
    window.location.assign(
      `/control-plane/sessions/${encodeURIComponent(sessionId)}/export.${ext}`
    );
    // 保险清理 busy（浏览器下载不触发 page unload）
    setTimeout(() => setBusy(null), 1500);
  };

  const baseStyle: React.CSSProperties = {
    fontSize: 12,
    padding: "4px 8px",
    border: "1px solid #3f3f46",
    borderRadius: 4,
    background: "#18181b",
    color: sessionId ? "#e4e4e7" : "#52525b",
    cursor: sessionId ? "pointer" : "not-allowed",
    transition: "background 0.15s",
  };

  return (
    <span style={{ display: "inline-flex", gap: 6, marginLeft: 8 }}>
      <button
        style={baseStyle}
        disabled={!sessionId || !!busy}
        onClick={() => trigger("json")}
        title="Export session log as JSON"
        aria-label="Export session as JSON"
      >
        {busy === "json" ? "…" : "↓ JSON"}
      </button>
      <button
        style={baseStyle}
        disabled={!sessionId || !!busy}
        onClick={() => trigger("md")}
        title="Export session log as Markdown"
        aria-label="Export session as Markdown"
      >
        {busy === "md" ? "…" : "↓ MD"}
      </button>
    </span>
  );
}
