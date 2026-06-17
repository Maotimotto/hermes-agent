/**
 * ControlPlaneSettingsPage — Claude / Codex provider 配置（V1.0.0 W10 收尾）。
 *
 * 数据流：
 *   - 读 GET /api/config → providers.claude / providers.codex
 *   - 写 PUT /api/config（merge 后整体保存）
 *   - API key 通过 GET /api/env / PUT /api/env / GET /api/env/reveal 管理，
 *     这样真正落到 ~/.hermes/.env 而不是 yaml 明文。
 *
 * 安全约束：
 *   - api_key 默认显示 redacted （••••）；用户主动点 Reveal 才调
 *     /api/env/reveal 拿明文，且 30 秒后自动隐藏。
 *   - 保存按钮在没改动时禁用，避免误覆盖手改的 yaml。
 *
 * Daemon 重启提示：写 config 不会自动让 daemon 重新注册 runtime。保存
 * 成功后弹一行黄色提示要求"重启 hermes daemon 生效"。
 */

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { EnvVarInfo } from "@/lib/api";
import { ControlPlaneTopBar } from "@/components/control-plane";
import { useProviders } from "@/hooks/control-plane";

type ClaudeConfig = {
  api_key?: string; // ENV reference name when persisted (we keep it implicit)
  model?: string;
  base_url?: string;
};

type CodexConfig = {
  codex_bin?: string;
  codex_home?: string;
  permission_profile?: string;
};

const ANTHROPIC_KEY_NAME = "ANTHROPIC_API_KEY";

export default function ControlPlaneSettingsPage() {
  const { providers } = useProviders();
  const [loading, setLoading] = useState(true);
  const [savingConfig, setSavingConfig] = useState(false);
  const [savingKey, setSavingKey] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  // Original (last server-side) values — for dirty diff.
  const [originalClaude, setOriginalClaude] = useState<ClaudeConfig>({});
  const [originalCodex, setOriginalCodex] = useState<CodexConfig>({});
  const [claude, setClaude] = useState<ClaudeConfig>({});
  const [codex, setCodex] = useState<CodexConfig>({});

  // Whole config we last fetched — we merge edits back into it on save.
  const [rawConfig, setRawConfig] = useState<Record<string, unknown> | null>(
    null,
  );

  // ENV var info for the API key (is_set, redacted_value).
  const [envInfo, setEnvInfo] = useState<EnvVarInfo | null>(null);
  const [keyDraft, setKeyDraft] = useState("");
  const [keyRevealed, setKeyRevealed] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [cfg, env] = await Promise.all([
          api.getConfig(),
          api.getEnvVars(),
        ]);
        if (cancelled) return;
        setRawConfig(cfg);

        const providers = (cfg?.providers ?? {}) as Record<string, unknown>;
        const cclaude = (providers.claude ?? {}) as ClaudeConfig;
        const ccodex = (providers.codex ?? {}) as CodexConfig;
        setClaude({ ...cclaude });
        setCodex({ ...ccodex });
        setOriginalClaude({ ...cclaude });
        setOriginalCodex({ ...ccodex });

        setEnvInfo(env[ANTHROPIC_KEY_NAME] ?? null);
      } catch (e) {
        if (!cancelled) setError(`Load failed: ${(e as Error).message}`);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Auto-hide revealed key after 30s
  useEffect(() => {
    if (!keyRevealed) return;
    const t = setTimeout(() => setKeyRevealed(null), 30_000);
    return () => clearTimeout(t);
  }, [keyRevealed]);

  const claudeDirty = useMemo(
    () => JSON.stringify(claude) !== JSON.stringify(originalClaude),
    [claude, originalClaude],
  );
  const codexDirty = useMemo(
    () => JSON.stringify(codex) !== JSON.stringify(originalCodex),
    [codex, originalCodex],
  );
  const anyDirty = claudeDirty || codexDirty;

  const claudeProviderStatus = providers.find((p) => p.kind === "claude");
  const codexProviderStatus = providers.find((p) => p.kind === "codex");

  const onSaveConfig = async () => {
    if (!rawConfig) return;
    setSavingConfig(true);
    setError(null);
    setInfo(null);
    try {
      const merged: Record<string, unknown> = { ...rawConfig };
      const providers = { ...((rawConfig.providers ?? {}) as Record<string, unknown>) };
      // Strip empty strings to keep yaml clean.
      providers.claude = stripEmpty({ ...claude });
      providers.codex = stripEmpty({ ...codex });
      merged.providers = providers;
      await api.saveConfig(merged);
      setOriginalClaude({ ...claude });
      setOriginalCodex({ ...codex });
      setRawConfig(merged);
      setInfo("Saved. Restart the Hermes daemon to apply runtime changes.");
    } catch (e) {
      setError(`Save failed: ${(e as Error).message}`);
    } finally {
      setSavingConfig(false);
    }
  };

  const onSaveKey = async () => {
    if (!keyDraft.trim()) return;
    setSavingKey(true);
    setError(null);
    setInfo(null);
    try {
      await api.setEnvVar(ANTHROPIC_KEY_NAME, keyDraft.trim());
      setKeyDraft("");
      const env = await api.getEnvVars();
      setEnvInfo(env[ANTHROPIC_KEY_NAME] ?? null);
      setInfo(`${ANTHROPIC_KEY_NAME} saved to ~/.hermes/.env. Restart daemon to apply.`);
    } catch (e) {
      setError(`Save key failed: ${(e as Error).message}`);
    } finally {
      setSavingKey(false);
    }
  };

  const onRevealKey = async () => {
    setError(null);
    try {
      const r = await api.revealEnvVar(ANTHROPIC_KEY_NAME);
      setKeyRevealed(r.value);
    } catch (e) {
      setError(`Reveal failed: ${(e as Error).message}`);
    }
  };

  const onClearKey = async () => {
    if (!confirm(`Clear ${ANTHROPIC_KEY_NAME} from ~/.hermes/.env?`)) return;
    setError(null);
    try {
      await api.deleteEnvVar(ANTHROPIC_KEY_NAME);
      const env = await api.getEnvVars();
      setEnvInfo(env[ANTHROPIC_KEY_NAME] ?? null);
      setInfo(`${ANTHROPIC_KEY_NAME} cleared.`);
    } catch (e) {
      setError(`Delete failed: ${(e as Error).message}`);
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
          padding: 24,
          color: "var(--midground, #ffe6cb)",
          maxWidth: 880,
          width: "100%",
          margin: "0 auto",
        }}
      >
        <h2
          style={{
            margin: "0 0 4px",
            fontSize: 22,
            letterSpacing: 0.5,
            fontFamily: "var(--theme-font-mono, monospace)",
          }}
        >
          Control Plane Settings
        </h2>
        <p
          style={{
            margin: "0 0 20px",
            color: "var(--color-text-secondary, #9ca3af)",
            fontSize: 13,
          }}
        >
          Provider configuration for the Hermes Control Plane daemon. Changes
          land in <code>~/.hermes/config.yaml</code> and{" "}
          <code>~/.hermes/.env</code>; restart the daemon to pick them up.
        </p>

        {error && <Banner kind="error">{error}</Banner>}
        {info && <Banner kind="info">{info}</Banner>}

        {loading ? (
          <Card>
            <div style={{ padding: 20, color: "var(--color-text-secondary)" }}>
              Loading config…
            </div>
          </Card>
        ) : (
          <>
            {/* Claude card */}
            <Card>
              <CardHeader
                title="Claude"
                badge={
                  claudeProviderStatus
                    ? claudeProviderStatus.available
                      ? { text: "READY", color: "#10b981" }
                      : { text: "DOWN", color: "#ef4444" }
                    : { text: "NOT REGISTERED", color: "#9ca3af" }
                }
              />
              <CardBody>
                <Field label="Model">
                  <input
                    type="text"
                    value={claude.model ?? ""}
                    placeholder="claude-opus-4-7"
                    onChange={(e) => setClaude({ ...claude, model: e.target.value })}
                    style={inputStyle()}
                  />
                </Field>
                <Field label="Base URL (optional)">
                  <input
                    type="text"
                    value={claude.base_url ?? ""}
                    placeholder="https://api.anthropic.com"
                    onChange={(e) =>
                      setClaude({ ...claude, base_url: e.target.value })
                    }
                    style={inputStyle()}
                  />
                </Field>

                <Field label={`API key (${ANTHROPIC_KEY_NAME})`}>
                  <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <input
                      type="password"
                      value={keyDraft}
                      placeholder={
                        envInfo?.is_set
                          ? envInfo.redacted_value || "••••••••"
                          : "Not set"
                      }
                      onChange={(e) => setKeyDraft(e.target.value)}
                      style={{ ...inputStyle(), flex: 1 }}
                    />
                    <button
                      onClick={onSaveKey}
                      disabled={savingKey || !keyDraft.trim()}
                      style={btnStyle("primary", savingKey || !keyDraft.trim())}
                    >
                      {savingKey ? "Saving…" : "Save"}
                    </button>
                    {envInfo?.is_set && (
                      <>
                        <button
                          onClick={onRevealKey}
                          style={btnStyle("ghost", false)}
                        >
                          {keyRevealed ? "Hide" : "Reveal"}
                        </button>
                        <button
                          onClick={onClearKey}
                          style={btnStyle("danger", false)}
                        >
                          Clear
                        </button>
                      </>
                    )}
                  </div>
                  {keyRevealed && (
                    <div
                      style={{
                        marginTop: 6,
                        padding: "6px 8px",
                        background: "rgba(245,158,11,0.10)",
                        border: "1px solid #fbbf24",
                        borderRadius: 4,
                        fontSize: 12,
                        fontFamily: "var(--theme-font-mono, monospace)",
                        color: "#fcd34d",
                        wordBreak: "break-all",
                      }}
                    >
                      {keyRevealed}
                      <span
                        style={{
                          marginLeft: 8,
                          fontSize: 10,
                          color: "var(--color-text-secondary)",
                        }}
                      >
                        (auto-hides in 30s)
                      </span>
                    </div>
                  )}
                </Field>
              </CardBody>
            </Card>

            {/* Codex card */}
            <Card>
              <CardHeader
                title="Codex"
                badge={
                  codexProviderStatus
                    ? codexProviderStatus.available
                      ? { text: "READY", color: "#10b981" }
                      : { text: "DOWN", color: "#ef4444" }
                    : { text: "NOT REGISTERED", color: "#9ca3af" }
                }
              />
              <CardBody>
                <Field label="codex binary path">
                  <input
                    type="text"
                    value={codex.codex_bin ?? ""}
                    placeholder="codex"
                    onChange={(e) =>
                      setCodex({ ...codex, codex_bin: e.target.value })
                    }
                    style={inputStyle()}
                  />
                </Field>
                <Field label="codex home (workdir for rollouts)">
                  <input
                    type="text"
                    value={codex.codex_home ?? ""}
                    placeholder="~/.codex"
                    onChange={(e) =>
                      setCodex({ ...codex, codex_home: e.target.value })
                    }
                    style={inputStyle()}
                  />
                </Field>
                <Field label="permission profile">
                  <select
                    value={codex.permission_profile ?? ""}
                    onChange={(e) =>
                      setCodex({ ...codex, permission_profile: e.target.value })
                    }
                    style={inputStyle()}
                  >
                    <option value="">(default)</option>
                    <option value="read_only">read_only</option>
                    <option value="trusted">trusted</option>
                    <option value="full_access">full_access</option>
                  </select>
                </Field>
              </CardBody>
            </Card>

            {/* Save bar */}
            <div
              style={{
                position: "sticky",
                bottom: 0,
                background: "color-mix(in srgb, var(--background-base, #041c1c) 90%, transparent)",
                paddingTop: 12,
                borderTop: anyDirty
                  ? "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 24%, transparent)"
                  : "1px solid transparent",
                display: "flex",
                justifyContent: "flex-end",
                gap: 8,
              }}
            >
              {anyDirty && (
                <span
                  style={{
                    color: "#fcd34d",
                    fontSize: 12,
                    alignSelf: "center",
                    marginRight: "auto",
                  }}
                >
                  Unsaved changes
                </span>
              )}
              <button
                onClick={() => {
                  setClaude({ ...originalClaude });
                  setCodex({ ...originalCodex });
                }}
                disabled={!anyDirty || savingConfig}
                style={btnStyle("ghost", !anyDirty || savingConfig)}
              >
                Discard
              </button>
              <button
                onClick={onSaveConfig}
                disabled={!anyDirty || savingConfig}
                style={btnStyle("primary", !anyDirty || savingConfig)}
              >
                {savingConfig ? "Saving…" : "Save changes"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── primitives ────────────────────────────────────────────────────────────────

function Banner({
  kind,
  children,
}: {
  kind: "info" | "error";
  children: React.ReactNode;
}) {
  const palette =
    kind === "error"
      ? { border: "#fca5a5", bg: "rgba(239,68,68,0.12)", color: "#fecaca" }
      : { border: "#fbbf24", bg: "rgba(245,158,11,0.12)", color: "#fcd34d" };
  return (
    <div
      role={kind === "error" ? "alert" : "status"}
      style={{
        padding: "8px 12px",
        border: `1px solid ${palette.border}`,
        background: palette.bg,
        color: palette.color,
        borderRadius: 6,
        fontSize: 13,
        marginBottom: 12,
      }}
    >
      {children}
    </div>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <section
      style={{
        marginBottom: 16,
        border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 14%, transparent)",
        borderRadius: 10,
        background: "color-mix(in srgb, var(--midground-base, #ffe6cb) 4%, var(--background-base, #041c1c))",
        overflow: "hidden",
      }}
    >
      {children}
    </section>
  );
}

function CardHeader({
  title,
  badge,
}: {
  title: string;
  badge?: { text: string; color: string };
}) {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "12px 16px",
        borderBottom: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 10%, transparent)",
      }}
    >
      <h3
        style={{
          margin: 0,
          fontSize: 15,
          fontFamily: "var(--theme-font-mono, monospace)",
          letterSpacing: 0.4,
        }}
      >
        {title}
      </h3>
      {badge && (
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "2px 8px",
            border: `1px solid ${badge.color}`,
            borderRadius: 4,
            color: badge.color,
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: 0.5,
          }}
        >
          <span
            aria-hidden
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: badge.color,
            }}
          />
          {badge.text}
        </span>
      )}
    </header>
  );
}

function CardBody({ children }: { children: React.ReactNode }) {
  return <div style={{ padding: 16, display: "grid", gap: 14 }}>{children}</div>;
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label
      style={{
        display: "grid",
        gap: 4,
        fontSize: 12,
        color: "var(--color-text-secondary, #9ca3af)",
      }}
    >
      <span style={{ letterSpacing: 0.4 }}>{label}</span>
      {children}
    </label>
  );
}

function inputStyle(): React.CSSProperties {
  return {
    padding: "8px 10px",
    border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 18%, transparent)",
    borderRadius: 6,
    background: "color-mix(in srgb, #000 25%, var(--background-base, #041c1c))",
    color: "var(--midground, #ffe6cb)",
    fontFamily: "var(--theme-font-mono, monospace)",
    fontSize: 13,
    width: "100%",
    boxSizing: "border-box",
  };
}

type BtnVariant = "primary" | "danger" | "ghost";
function btnStyle(variant: BtnVariant, disabled: boolean): React.CSSProperties {
  const base: React.CSSProperties = {
    padding: "7px 14px",
    borderRadius: 6,
    fontSize: 12,
    fontFamily: "var(--theme-font-mono, monospace)",
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1,
  };
  if (variant === "primary") {
    return {
      ...base,
      border: "1px solid #3b82f6",
      background: "#3b82f6",
      color: "white",
      fontWeight: 600,
    };
  }
  if (variant === "danger") {
    return {
      ...base,
      border: "1px solid #ef4444",
      background: "transparent",
      color: "#ef4444",
    };
  }
  return {
    ...base,
    border: "1px solid color-mix(in srgb, var(--midground-base, #ffe6cb) 18%, transparent)",
    background: "transparent",
    color: "var(--midground, #ffe6cb)",
  };
}

function stripEmpty<T extends Record<string, unknown>>(obj: T): T {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (typeof v === "string" && v.trim() === "") continue;
    if (v === undefined || v === null) continue;
    out[k] = v;
  }
  return out as T;
}
