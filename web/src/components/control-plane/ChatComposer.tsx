/**
 * ChatComposer — 事件面板底部的发送 turn 输入区。
 *
 * 受控组件：value/onChange 由父组件管理；Ctrl/Cmd+Enter 直接触发 onSend，
 * 发送中 / 空值时按钮禁用。
 */

export type ChatComposerProps = {
  value: string;
  onChange: (next: string) => void;
  onSend: () => void;
  sending: boolean;
};

export function ChatComposer({
  value,
  onChange,
  onSend,
  sending,
}: ChatComposerProps) {
  const disabled = sending || !value.trim();
  return (
    <div
      style={{
        padding: 10,
        borderTop: "1px solid var(--color-border)",
        background: "var(--color-secondary)",
        display: "flex",
        gap: 8,
      }}
    >
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            onSend();
          }
        }}
        placeholder="Send a turn to this session… (Ctrl/Cmd+Enter)"
        rows={2}
        style={{
          flex: 1,
          padding: 8,
          fontSize: 13,
          border: "1px solid var(--color-input)",
          borderRadius: 4,
          background: "var(--color-card)",
          color: "var(--color-card-foreground)",
          resize: "vertical",
          fontFamily: "inherit",
          boxSizing: "border-box",
        }}
      />
      <button
        onClick={onSend}
        disabled={disabled}
        style={{
          padding: "0 14px",
          background: disabled
            ? "color-mix(in srgb, var(--color-muted-foreground) 55%, transparent)"
            : "var(--color-primary)",
          color: "var(--color-primary-foreground)",
          border: "none",
          borderRadius: 4,
          cursor: disabled ? "not-allowed" : "pointer",
          fontSize: 13,
          fontWeight: 600,
          whiteSpace: "nowrap",
        }}
      >
        {sending ? "Sending…" : "Send"}
      </button>
    </div>
  );
}
