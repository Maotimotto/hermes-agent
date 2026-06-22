/**
 * TemplatePicker — 选择并渲染任务模板，输出 prompt 文本。
 *
 * 使用方式：<TemplatePicker onUse={(rendered) => setPrompt(rendered)} />
 *
 * V1.1 P1 任务模板 — 前端 Wave B
 */
import { useCallback, useState } from "react";
import { useTemplates } from "@/hooks/control-plane";
import type { Template } from "@/hooks/control-plane";

export interface TemplatePickerProps {
  /** 回调：渲染后的 prompt 文本 → 由调用方塞入 ChatComposer */
  onUse: (rendered: string) => void;
}

export function TemplatePicker({ onUse }: TemplatePickerProps) {
  const { templates, loading, error, renderTemplate } = useTemplates();
  const [selected, setSelected] = useState<Template | null>(null);
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [renderError, setRenderError] = useState<string | null>(null);
  const [rendering, setRendering] = useState(false);

  const handleSelect = useCallback(
    (tpl: Template) => {
      setSelected(tpl);
      setRenderError(null);
      // 初始化 param 默认值
      const defaults: Record<string, string> = {};
      for (const p of tpl.params) {
        defaults[p.name] = p.default ?? "";
      }
      setParamValues(defaults);
    },
    []
  );

  const handleRender = useCallback(async () => {
    if (!selected) return;
    setRendering(true);
    setRenderError(null);
    const result = await renderTemplate(selected.id, paramValues);
    setRendering(false);
    if (result !== null) {
      onUse(result);
      setSelected(null);
    } else {
      setRenderError("渲染失败，请检查参数");
    }
  }, [selected, paramValues, renderTemplate, onUse]);

  // Collapsed / no session: 不渲染
  if (loading) return <span style={{ fontSize: 12, color: "#71717a" }}>加载模板…</span>;
  if (error) return <span style={{ fontSize: 12, color: "#ef4444" }}>模板加载失败</span>;
  if (templates.length === 0) return null;

  // 未选：显示模板列表
  if (!selected) {
    return (
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {templates.map((tpl) => (
          <button
            key={tpl.id}
            onClick={() => handleSelect(tpl)}
            title={tpl.description || tpl.name}
            style={{
              fontSize: 12,
              padding: "4px 10px",
              borderRadius: 4,
              border: "1px solid #3f3f46",
              background: "#18181b",
              color: "#e4e4e7",
              cursor: "pointer",
            }}
          >
            {tpl.name}
          </button>
        ))}
      </div>
    );
  }

  // 已选：展示参数输入
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ fontSize: 13, color: "#a1a1aa" }}>
        模板: <strong style={{ color: "#e4e4e7" }}>{selected.name}</strong>
        <button
          onClick={() => setSelected(null)}
          style={{
            marginLeft: 8,
            fontSize: 11,
            color: "#71717a",
            cursor: "pointer",
            background: "none",
            border: "none",
          }}
        >
          ✕ 取消
        </button>
      </div>

      {selected.params.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {selected.params.map((p) => (
            <label
              key={p.name}
              style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}
            >
              <span style={{ minWidth: 80, color: "#a1a1aa" }}>{p.name}:</span>
              <input
                value={paramValues[p.name] ?? ""}
                onChange={(e) =>
                  setParamValues((prev) => ({ ...prev, [p.name]: e.target.value }))
                }
                placeholder={p.description || p.name}
                style={{
                  flex: 1,
                  padding: "3px 6px",
                  fontSize: 12,
                  background: "#09090b",
                  border: "1px solid #3f3f46",
                  borderRadius: 4,
                  color: "#e4e4e7",
                }}
              />
            </label>
          ))}
        </div>
      )}

      {renderError && <span style={{ fontSize: 11, color: "#ef4444" }}>{renderError}</span>}

      <button
        onClick={handleRender}
        disabled={rendering}
        style={{
          alignSelf: "flex-start",
          fontSize: 12,
          padding: "4px 12px",
          borderRadius: 4,
          background: "#6366f1",
          color: "#fff",
          border: "none",
          cursor: rendering ? "wait" : "pointer",
          opacity: rendering ? 0.7 : 1,
        }}
      >
        {rendering ? "渲染中…" : "填入 Prompt"}
      </button>
    </div>
  );
}
