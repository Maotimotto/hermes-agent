/**
 * HistoryPage — Sessions 历史浏览（V1.0.0 W10 收尾）。
 *
 * 与 ControlPlanePage 互补：那边是"现在正在发生什么"（活跃会话流），这边
 * 是"过去发生了什么"。功能：
 *   - 表格列出所有 session（status / runtime / model / repo / 时间）
 *   - 状态过滤下拉 + 关键字搜索（client side）
 *   - 翻页（limit/offset）
 *   - 行操作：View（跳到 /control-plane 并预选中该 session）/ Delete
 */

import { useNavigate } from "react-router-dom";
import { fetchJSON } from "@/lib/api";
import { useSessionsHistory, useDaemonStatus } from "@/hooks/control-plane";
import type { StatusFilter } from "@/hooks/control-plane/useSessionsHistory";
import { ControlPlaneTopBar } from "@/components/control-plane";
import { STATUS_COLORS } from "@/pages/control-plane/types";
import type { SessionRecord } from "@/pages/control-plane/types";

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "running", label: "Running" },
  { value: "waiting", label: "Waiting" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
  { value: "stopped", label: "Stopped" },
  { value: "created", label: "Created" },
];

export default function HistoryPage() {
  const navigate = useNavigate();
  const { overall } = useDaemonStatus();
  const {
    rows,
    filteredRows,
    loading,
    error,
    page,
    pageSize,
    query,
    setQuery,
    status,
    setStatus,
    setPage,
    refresh,
    removeRow,
  } = useSessionsHistory();

  const onView = (sid: string) => {
    navigate(`/control-plane?session=${encodeURIComponent(sid)}`);
  };

  const onDelete = async (s: SessionRecord) => {
    if (!confirm(`Delete session ${s.id.slice(0, 16)}?`)) return;
    try {
      await fetchJSON(`/control-plane/sessions/${s.id}`, { method: "DELETE" });
      removeRow(s.id);
    } catch (e) {
      alert(`Delete failed: ${(e as Error).message}`);
    }
  };

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

      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: 16,
          color: "var(--midground, #ffe6cb)",
        }}
      >
        {/* Toolbar */}
        <div
          style={{
            display: "flex",
            gap: 8,
            marginBottom: 12,
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search id / repo / model…"
            style={{
              flex: 1,
              minWidth: 240,
              padding: "6px 10px",
              border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 18%, transparent)",
              borderRadius: 6,
              background: "color-mix(in srgb, #000 25%, var(--background-base, #041c1c))",
              color: "var(--midground, #ffe6cb)",
              fontSize: 13,
              fontFamily: "var(--theme-font-mono, monospace)",
            }}
          />
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as StatusFilter);
              setPage(0);
            }}
            style={{
              padding: "6px 10px",
              border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 18%, transparent)",
              borderRadius: 6,
              background: "color-mix(in srgb, #000 25%, var(--background-base, #041c1c))",
              color: "var(--midground, #ffe6cb)",
              fontSize: 13,
            }}
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <button
            onClick={refresh}
            disabled={loading}
            style={{
              padding: "6px 10px",
              border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 24%, transparent)",
              borderRadius: 6,
              background: "transparent",
              color: "var(--midground, #ffe6cb)",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            {loading ? "Loading…" : "↻ Refresh"}
          </button>
          <span style={{ color: "var(--color-text-secondary, #6b7280)", fontSize: 12 }}>
            {filteredRows.length} of {rows.length} on page {page + 1}
          </span>
        </div>

        {error && (
          <div
            style={{
              padding: "8px 12px",
              border: "1px solid #fca5a5",
              background: "rgba(239,68,68,0.12)",
              color: "#fecaca",
              borderRadius: 6,
              fontSize: 13,
              marginBottom: 12,
            }}
          >
            {error}
          </div>
        )}

        {overall === "down" && (
          <div
            style={{
              padding: "8px 12px",
              border: "1px solid #fbbf24",
              background: "rgba(245,158,11,0.12)",
              color: "#fcd34d",
              borderRadius: 6,
              fontSize: 13,
              marginBottom: 12,
            }}
          >
            Daemon is offline — showing last cached results.
          </div>
        )}

        {/* Table */}
        <div
          style={{
            border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 14%, transparent)",
            borderRadius: 8,
            overflow: "hidden",
          }}
        >
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: 13,
              fontFamily: "var(--theme-font-mono, monospace)",
            }}
          >
            <thead
              style={{
                background: "color-mix(in srgb, var(--midground-base, #ffe6cb) 8%, var(--background-base, #041c1c))",
                color: "var(--color-text-secondary, #9ca3af)",
                fontSize: 11,
                letterSpacing: 0.4,
                textTransform: "uppercase",
              }}
            >
              <tr>
                <Th>Status</Th>
                <Th>Session</Th>
                <Th>Runtime · Model</Th>
                <Th>Repo</Th>
                <Th>Started</Th>
                <Th>Ended</Th>
                <Th align="right">Actions</Th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.length === 0 && !loading && (
                <tr>
                  <td
                    colSpan={7}
                    style={{
                      padding: 24,
                      textAlign: "center",
                      color: "var(--color-text-secondary, #6b7280)",
                    }}
                  >
                    {rows.length === 0
                      ? "No sessions match the current filter."
                      : "No matches in this page — try broadening the search."}
                  </td>
                </tr>
              )}
              {filteredRows.map((s) => (
                <tr
                  key={s.id}
                  style={{
                    borderTop: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 8%, transparent)",
                  }}
                >
                  <Td>
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                        color: STATUS_COLORS[s.status] || "var(--midground, #ffe6cb)",
                        fontWeight: 600,
                      }}
                    >
                      <span
                        aria-hidden
                        style={{
                          width: 7,
                          height: 7,
                          borderRadius: "50%",
                          background: STATUS_COLORS[s.status] || "#6b7280",
                        }}
                      />
                      {s.status}
                    </span>
                  </Td>
                  <Td>
                    <span title={s.id}>{s.id.slice(0, 16)}</span>
                  </Td>
                  <Td>
                    <span style={{ color: "var(--color-text-secondary, #9ca3af)" }}>
                      {s.runtime_kind}
                    </span>{" "}
                    · {s.model}
                  </Td>
                  <Td>
                    <span
                      title={s.repo_path || ""}
                      style={{
                        display: "inline-block",
                        maxWidth: 280,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        verticalAlign: "middle",
                      }}
                    >
                      {s.repo_path || (
                        <span style={{ color: "var(--color-text-secondary, #6b7280)" }}>—</span>
                      )}
                    </span>
                  </Td>
                  <Td>{formatTime(s.started_at)}</Td>
                  <Td>{s.ended_at ? formatTime(s.ended_at) : <Dim>—</Dim>}</Td>
                  <Td align="right">
                    <button
                      onClick={() => onView(s.id)}
                      style={tableBtnStyle("primary")}
                    >
                      View
                    </button>{" "}
                    <button
                      onClick={() => onDelete(s)}
                      style={tableBtnStyle("danger")}
                    >
                      Delete
                    </button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginTop: 12,
            color: "var(--color-text-secondary, #9ca3af)",
            fontSize: 12,
          }}
        >
          <span>
            Page {page + 1} · {pageSize} per page
          </span>
          <div style={{ display: "flex", gap: 6 }}>
            <button
              onClick={() => setPage(Math.max(0, page - 1))}
              disabled={page === 0 || loading}
              style={tableBtnStyle("ghost")}
            >
              ← Prev
            </button>
            <button
              onClick={() => setPage(page + 1)}
              disabled={loading || rows.length < pageSize}
              style={tableBtnStyle("ghost")}
            >
              Next →
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── helpers ───────────────────────────────────────────────────────────────────

function Th({
  children,
  align,
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      style={{
        textAlign: align ?? "left",
        padding: "8px 10px",
        fontWeight: 600,
      }}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  align,
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <td
      style={{
        padding: "8px 10px",
        textAlign: align ?? "left",
        verticalAlign: "middle",
      }}
    >
      {children}
    </td>
  );
}

function Dim({ children }: { children: React.ReactNode }) {
  return (
    <span style={{ color: "var(--color-text-secondary, #6b7280)" }}>{children}</span>
  );
}

function formatTime(ts: string): string {
  try {
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    return d.toLocaleString();
  } catch {
    return ts;
  }
}

type BtnVariant = "primary" | "danger" | "ghost";

function tableBtnStyle(variant: BtnVariant): React.CSSProperties {
  if (variant === "primary") {
    return {
      padding: "3px 9px",
      border: "1px solid #3b82f6",
      borderRadius: 4,
      background: "#3b82f6",
      color: "white",
      cursor: "pointer",
      fontSize: 11,
      fontFamily: "var(--theme-font-mono, monospace)",
    };
  }
  if (variant === "danger") {
    return {
      padding: "3px 9px",
      border: "1px solid #ef4444",
      borderRadius: 4,
      background: "transparent",
      color: "#ef4444",
      cursor: "pointer",
      fontSize: 11,
      fontFamily: "var(--theme-font-mono, monospace)",
    };
  }
  return {
    padding: "3px 9px",
    border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 18%, transparent)",
    borderRadius: 4,
    background: "transparent",
    color: "var(--midground, #ffe6cb)",
    cursor: "pointer",
    fontSize: 11,
    fontFamily: "var(--theme-font-mono, monospace)",
  };
}
