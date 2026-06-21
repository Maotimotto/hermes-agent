/**
 * ErrorBanner — 顶部错误横幅（V1.1 错误恢复 Wave D）。
 *
 * 与 ErrorToast 的区别：
 *   - ErrorToast 是右下角的临时提示，自己消失；适合「拉数据失败」这类
 *     可以下次重试的网络抖动。
 *   - ErrorBanner 是顶部的持久横幅，需要用户主动 dismiss；适合 turn.failed
 *     这种需要决策的终态错误，以及 503 provider_unavailable 这种带操作建议
 *     的 API 错误。
 *
 * 数据源：
 *   - source="event"  → 来自最近一次 turn.failed 事件，附 retryable / code
 *   - source="api"    → 来自本地 API 调用（如 sendTurn 拒绝 503）
 *
 * 设计：单 banner 同时只渲染一条；onDismiss 由父组件管理状态。
 */

import { motion } from "motion/react";

export type ErrorBannerSource = "event" | "api";

export type ErrorBannerProps = {
  /** 一行简洁标题。e.g. "Turn failed" / "Provider unavailable" */
  title: string;
  /** 详情正文（多行也行）。e.g. error message / hint */
  message: string;
  /** 错误码（可选）— 渲染成右上角小标签 */
  code?: string;
  /** 是否可重试（仅事件态用）。true 时主按钮显示「Retry」 */
  retryable?: boolean;
  /** 数据源 — 影响图标颜色 */
  source?: ErrorBannerSource;
  /** 点击 retry 按钮回调；不传则不显示该按钮 */
  onRetry?: () => void;
  /** dismiss 回调；不传则只在事件刷新时被替换 */
  onDismiss?: () => void;
  /** 操作提示（hint）— 渲染为副标题，如「请检查 API key」 */
  hint?: string;
};

export function ErrorBanner({
  title,
  message,
  code,
  retryable,
  source = "event",
  onRetry,
  onDismiss,
  hint,
}: ErrorBannerProps) {
  const accent = source === "api" ? "#dc2626" : "#b91c1c";
  return (
    <motion.div
      role="alert"
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      style={{
        margin: "8px 12px 0",
        padding: "10px 14px",
        background: "#fef2f2",
        border: `1px solid ${accent}`,
        borderLeft: `4px solid ${accent}`,
        borderRadius: 6,
        color: "#7f1d1d",
        fontSize: 13,
        boxShadow: "0 1px 2px rgba(0,0,0,0.04)",
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
      }}
    >
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke={accent}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ flexShrink: 0, marginTop: 1 }}
        aria-hidden
      >
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="8" x2="12" y2="12" />
        <line x1="12" y1="16" x2="12.01" y2="16" />
      </svg>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontWeight: 700,
          }}
        >
          <span>{title}</span>
          {code && (
            <span
              style={{
                padding: "1px 6px",
                borderRadius: 3,
                background: "color-mix(in srgb, currentColor 12%, transparent)",
                fontSize: 11,
                fontFamily: "var(--theme-font-mono, monospace)",
              }}
            >
              {code}
            </span>
          )}
          {retryable === true && (
            <span
              style={{
                padding: "1px 6px",
                borderRadius: 3,
                background: "color-mix(in srgb, #f59e0b 22%, transparent)",
                color: "#92400e",
                fontSize: 11,
                fontWeight: 600,
              }}
            >
              retryable
            </span>
          )}
        </div>
        <div style={{ marginTop: 4, lineHeight: 1.5, wordBreak: "break-word" }}>
          {message}
        </div>
        {hint && (
          <div
            style={{
              marginTop: 4,
              fontSize: 12,
              color: "#991b1b",
              opacity: 0.85,
            }}
          >
            {hint}
          </div>
        )}
      </div>
      <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            style={{
              padding: "4px 10px",
              fontSize: 12,
              fontWeight: 600,
              border: `1px solid ${accent}`,
              background: accent,
              color: "white",
              borderRadius: 4,
              cursor: "pointer",
            }}
          >
            Retry
          </button>
        )}
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            aria-label="Dismiss"
            style={{
              padding: "4px 8px",
              fontSize: 12,
              border: `1px solid ${accent}`,
              background: "transparent",
              color: accent,
              borderRadius: 4,
              cursor: "pointer",
            }}
          >
            Dismiss
          </button>
        )}
      </div>
    </motion.div>
  );
}
