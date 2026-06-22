/**
 * XtermViewer — 嵌入式 xterm.js 终端回放组件。
 *
 * 用于 ToolCallCard 的 output 区域，把工具输出（含 ANSI 颜色控制码）渲染为
 * 真实终端外观，而不是 <pre> 文本。只读、可滚动、自适应宽度。
 *
 * V1.1 P1 — xterm 终端模拟
 */
import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

export interface XtermViewerProps {
  /** 工具输出原始文本（保留 ANSI 控制码） */
  data: string;
  /** 高度 px，默认 280 */
  height?: number;
}

export function XtermViewer({ data, height = 280 }: XtermViewerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);

  // 初始化终端 — mount 一次
  useEffect(() => {
    if (!containerRef.current) return;
    const term = new Terminal({
      cursorBlink: false,
      disableStdin: true,
      convertEol: true,
      fontFamily:
        "'JetBrains Mono', 'Cascadia Mono', 'Fira Code', 'Source Code Pro', Menlo, Consolas, 'DejaVu Sans Mono', monospace",
      fontSize: 12,
      lineHeight: 1.3,
      scrollback: 5000,
      theme: {
        background: "#0c0d10",
        foreground: "#e4e4e7",
        cursor: "transparent",
        black: "#1f1f1f",
        red: "#ef4444",
        green: "#22c55e",
        yellow: "#eab308",
        blue: "#3b82f6",
        magenta: "#a855f7",
        cyan: "#06b6d4",
        white: "#e4e4e7",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    try {
      fit.fit();
    } catch {
      // 容器尺寸尚未确定时 fit 会抛 — 忽略，下次 ResizeObserver 会重试
    }
    termRef.current = term;
    fitRef.current = fit;

    const ro = new ResizeObserver(() => {
      try {
        fit.fit();
      } catch {
        // ignore
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, []);

  // 数据变更 — 全量重写（轻量，工具输出通常 < 几 KB）
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    term.reset();
    if (data) term.write(data);
  }, [data]);

  return (
    <div
      ref={containerRef}
      style={{
        height,
        background: "#0c0d10",
        borderRadius: 4,
        padding: 6,
        overflow: "hidden",
      }}
    />
  );
}
