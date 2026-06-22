/**
 * CreateSessionForm — sessions 列表上方的内嵌创建表单。
 *
 * 只负责表单状态与提交，不直接打 API；submit 通过 props 回调（usually
 * useSessions().createSession）让父组件控制业务流程。
 */

import { useState } from "react";
import type { CreateSessionInput } from "@/hooks/control-plane";

export type CreateSessionFormProps = {
  onSubmit: (input: CreateSessionInput) => Promise<unknown> | void;
  onCancel: () => void;
};

const FIELD_STYLE: React.CSSProperties = {
  display: "block",
  width: "100%",
  marginTop: 4,
  padding: "4px 6px",
  fontSize: 12,
  border: "1px solid var(--color-input)",
  borderRadius: 4,
  background: "var(--color-card)",
  color: "var(--color-card-foreground)",
  boxSizing: "border-box",
};

export function CreateSessionForm({
  onSubmit,
  onCancel,
}: CreateSessionFormProps) {
  const [runtime, setRuntime] = useState<"claude" | "codex">("claude");
  const [model, setModel] = useState("claude-opus-4-7");
  const [repoPath, setRepoPath] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      await onSubmit({
        runtime_kind: runtime,
        model,
        repo_path: repoPath,
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      style={{
        padding: 12,
        background: "var(--color-secondary)",
        borderBottom: "1px solid var(--color-border)",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <label style={{ fontSize: 12, color: "var(--color-card-foreground)" }}>
        Runtime
        <select
          value={runtime}
          onChange={(e) =>
            setRuntime(e.target.value as "claude" | "codex")
          }
          style={FIELD_STYLE}
        >
          <option value="claude">claude</option>
          <option value="codex">codex</option>
        </select>
      </label>
      <label style={{ fontSize: 12, color: "var(--color-card-foreground)" }}>
        Model
        <input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          style={FIELD_STYLE}
        />
      </label>
      <label style={{ fontSize: 12, color: "var(--color-card-foreground)" }}>
        Repo path (optional)
        <input
          value={repoPath}
          onChange={(e) => setRepoPath(e.target.value)}
          placeholder="/home/.../project"
          style={FIELD_STYLE}
        />
      </label>
      <div style={{ display: "flex", gap: 6 }}>
        <button
          onClick={handleSubmit}
          disabled={submitting}
          style={{
            flex: 1,
            padding: "6px 10px",
            background: submitting
              ? "color-mix(in srgb, var(--color-muted-foreground) 55%, transparent)"
              : "#10b981",
            color: "var(--color-primary-foreground)",
            border: "none",
            borderRadius: 4,
            cursor: submitting ? "not-allowed" : "pointer",
            fontSize: 12,
          }}
        >
          {submitting ? "Creating…" : "Create"}
        </button>
        <button
          onClick={onCancel}
          disabled={submitting}
          style={{
            flex: 1,
            padding: "6px 10px",
            background: "var(--color-card)",
            color: "var(--color-card-foreground)",
            border: "1px solid var(--color-border)",
            borderRadius: 4,
            cursor: submitting ? "not-allowed" : "pointer",
            fontSize: 12,
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
