/**
 * useTemplates — CRUD hook for task templates (P1 任务模板).
 *
 * GET /control-plane/templates
 * POST /control-plane/templates
 * PATCH /control-plane/templates/:id
 * DELETE /control-plane/templates/:id
 * POST /control-plane/templates/:id/render
 */
import { useCallback, useEffect, useState } from "react";

export interface TemplateParam {
  name: string;
  description?: string;
  default?: string;
}

export interface Template {
  id: string;
  name: string;
  description: string;
  body: string;
  params: TemplateParam[];
}

export interface UseTemplatesReturn {
  templates: Template[];
  loading: boolean;
  error: string | null;
  refresh: () => void;
  createTemplate: (data: Omit<Template, "id">) => Promise<Template | null>;
  updateTemplate: (id: string, data: Partial<Omit<Template, "id">>) => Promise<boolean>;
  deleteTemplate: (id: string) => Promise<boolean>;
  renderTemplate: (id: string, params: Record<string, string>) => Promise<string | null>;
}

export function useTemplates(): UseTemplatesReturn {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  // 拉取放在 effect 内。手动 refresh 通过 reloadKey 触发。
  useEffect(() => {
    let cancelled = false;
    /* eslint-disable react-hooks/set-state-in-effect -- 异步 fetch 后必然 setState */
    setLoading(true);
    setError(null);
    fetch("/control-plane/templates")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) setTemplates(data.templates ?? []);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    /* eslint-enable react-hooks/set-state-in-effect */
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const refresh = useCallback(() => {
    setReloadKey((k) => k + 1);
  }, []);

  const createTemplate = useCallback(
    async (data: Omit<Template, "id">): Promise<Template | null> => {
      try {
        const r = await fetch("/control-plane/templates", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const tpl: Template = await r.json();
        setTemplates((prev) => [...prev, tpl]);
        return tpl;
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
        return null;
      }
    },
    []
  );

  const updateTemplate = useCallback(
    async (id: string, data: Partial<Omit<Template, "id">>): Promise<boolean> => {
      try {
        const r = await fetch(`/control-plane/templates/${encodeURIComponent(id)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const updated: Template = await r.json();
        setTemplates((prev) => prev.map((t) => (t.id === id ? updated : t)));
        return true;
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
        return false;
      }
    },
    []
  );

  const deleteTemplate = useCallback(async (id: string): Promise<boolean> => {
    try {
      const r = await fetch(`/control-plane/templates/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setTemplates((prev) => prev.filter((t) => t.id !== id));
      return true;
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    }
  }, []);

  const renderTemplate = useCallback(
    async (id: string, params: Record<string, string>): Promise<string | null> => {
      try {
        const r = await fetch(
          `/control-plane/templates/${encodeURIComponent(id)}/render`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ params }),
          }
        );
        if (!r.ok) {
          const body = await r.json().catch(() => ({}));
          throw new Error(body.message || `HTTP ${r.status}`);
        }
        const data = await r.json();
        return data.rendered ?? null;
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
        return null;
      }
    },
    []
  );

  return {
    templates,
    loading,
    error,
    refresh,
    createTemplate,
    updateTemplate,
    deleteTemplate,
    renderTemplate,
  };
}
