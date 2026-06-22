/**
 * ControlPlaneTopBar — 控制台顶栏：daemon health + provider 状态徽标。
 *
 * 左侧：DAEMON 状态点（绿/黄/红）+ 文字（OK / DEGRADED / OFFLINE）。
 *       hover 显示子系统细节（store / workspace / runtime）。
 * 中间：留白，让上层页面有空间继续放标题或操作。
 * 右侧：每个已注册 provider 一个 ProviderBadge，显示 kind + 可用性 +
 *       active sessions 计数。
 *
 * 数据来自 useDaemonStatus + useProviders（自带轮询）。失败时降级到
 * "OFFLINE" 红点 + 提示。
 */

import type { CSSProperties, ReactNode } from "react";
import { useDaemonStatus, useProviders } from "@/hooks/control-plane";
import type {
  DaemonHealth,
  DaemonOverall,
  ProviderInfo,
} from "@/hooks/control-plane";
import { ThemeToggle } from "./ThemeToggle";

const DAEMON_STYLES: Record<DaemonOverall, { dot: string; label: string; tone: string }> = {
  ok: { dot: "#10b981", label: "OK", tone: "#065f46" },
  degraded: { dot: "#f59e0b", label: "DEGRADED", tone: "#92400e" },
  down: { dot: "#ef4444", label: "OFFLINE", tone: "#991b1b" },
};

export type ControlPlaneTopBarProps = {
  style?: CSSProperties;
  /** 额外内容（页面级操作按钮等）放到右侧 provider 徽标之前。 */
  trailing?: ReactNode;
};

export function ControlPlaneTopBar({ style, trailing }: ControlPlaneTopBarProps) {
  const { overall, health, error, lastOkAt } = useDaemonStatus();
  const { providers, error: provError } = useProviders();

  return (
    <div
      role="status"
      aria-label="Control plane status bar"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "8px 12px",
        borderBottom: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 14%, transparent)",
        background: "color-mix(in srgb, var(--midground-base, #ffe6cb) 4%, var(--background-base, #041c1c))",
        fontFamily: "var(--theme-font-mono, monospace)",
        fontSize: 12,
        ...style,
      }}
    >
      <DaemonPill overall={overall} health={health} error={error} lastOkAt={lastOkAt} />
      <div style={{ flex: 1 }} />
      {trailing}
      <ThemeToggle />
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {providers.length === 0 && !provError && (
          <span style={{ color: "var(--color-text-secondary, #6b7280)" }}>
            no providers registered
          </span>
        )}
        {providers.length === 0 && provError && (
          <span style={{ color: "#fca5a5" }} title={provError}>
            providers: error
          </span>
        )}
        {providers.map((p) => (
          <ProviderBadge key={p.kind} provider={p} />
        ))}
      </div>
    </div>
  );
}

// ── DaemonPill ────────────────────────────────────────────────────────────────

function DaemonPill({
  overall,
  health,
  error,
  lastOkAt,
}: {
  overall: DaemonOverall;
  health: DaemonHealth | null;
  error: string | null;
  lastOkAt: number | null;
}) {
  const s = DAEMON_STYLES[overall];
  const tooltip = buildTooltip(overall, health, error, lastOkAt);

  return (
    <span
      title={tooltip}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "3px 10px",
        border: `1px solid ${s.dot}`,
        background: "color-mix(in srgb, currentColor 0%, transparent)",
        borderRadius: 999,
        color: s.tone,
        fontWeight: 700,
        letterSpacing: 0.5,
      }}
    >
      <span
        aria-hidden
        style={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: s.dot,
          boxShadow: overall === "ok" ? `0 0 0 3px ${s.dot}33` : "none",
        }}
      />
      DAEMON · {s.label}
    </span>
  );
}

function buildTooltip(
  overall: DaemonOverall,
  health: DaemonHealth | null,
  error: string | null,
  lastOkAt: number | null,
): string {
  const lines: string[] = [];
  lines.push(`Status: ${overall.toUpperCase()}`);
  if (error) lines.push(`Error: ${error}`);
  if (health?.store) {
    lines.push(
      `Store: ${health.store.status ?? "?"}` +
        (health.store.detail ? ` (${health.store.detail})` : "") +
        (typeof health.store.session_count === "number"
          ? ` · ${health.store.session_count} sessions`
          : ""),
    );
  }
  if (health?.workspace) {
    lines.push(
      `Workspace: ${health.workspace.status ?? "?"}` +
        (health.workspace.detail ? ` (${health.workspace.detail})` : "") +
        (typeof health.workspace.count === "number"
          ? ` · ${health.workspace.count} workspaces`
          : ""),
    );
  }
  if (health?.runtime) {
    const reg = health.runtime.registered?.join(", ") || "—";
    lines.push(
      `Runtime: ${health.runtime.status ?? "?"} · registered=${reg}` +
        (typeof health.runtime.active_turns === "number"
          ? ` · ${health.runtime.active_turns} active turns`
          : ""),
    );
  }
  if (lastOkAt) {
    const ago = Math.round((Date.now() - lastOkAt) / 1000);
    lines.push(`Last OK: ${ago}s ago`);
  }
  return lines.join("\n");
}

// ── ProviderBadge ─────────────────────────────────────────────────────────────

function ProviderBadge({ provider }: { provider: ProviderInfo }) {
  const dot = provider.available ? "#10b981" : "#ef4444";
  const text = provider.available ? "READY" : "DOWN";
  const tone = provider.available ? "#065f46" : "#991b1b";
  const tooltip = [
    `${provider.kind}: ${text}`,
    provider.message ? `Message: ${provider.message}` : "",
    provider.version ? `Version: ${provider.version}` : "",
    typeof provider.latency_ms === "number" && provider.latency_ms >= 0
      ? `Latency: ${provider.latency_ms}ms`
      : "",
    typeof provider.active_sessions === "number"
      ? `Active sessions: ${provider.active_sessions}`
      : "",
  ]
    .filter(Boolean)
    .join("\n");

  return (
    <span
      title={tooltip}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "3px 8px",
        border: `1px solid ${dot}`,
        borderRadius: 4,
        color: tone,
        background: provider.available
          ? "color-mix(in srgb, #10b981 8%, transparent)"
          : "color-mix(in srgb, #ef4444 8%, transparent)",
      }}
    >
      <span
        aria-hidden
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: dot,
        }}
      />
      <span style={{ fontWeight: 700 }}>{provider.kind.toUpperCase()}</span>
      <span style={{ color: "var(--color-text-secondary, #6b7280)" }}>·</span>
      <span style={{ color: "var(--color-text-secondary, #6b7280)" }}>{text}</span>
      {typeof provider.active_sessions === "number" &&
        provider.active_sessions > 0 && (
          <span
            style={{
              marginLeft: 4,
              padding: "0 5px",
              borderRadius: 3,
              background: "color-mix(in srgb, currentColor 14%, transparent)",
              fontSize: 10,
            }}
          >
            {provider.active_sessions}
          </span>
        )}
    </span>
  );
}
