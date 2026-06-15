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
        borderTop: "1px solid #e5e7eb",
        background: "#f9fafb",
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
          border: "1px solid #d1d5db",
          borderRadius: 4,
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
          background: disabled ? "#9ca3af" : "#3b82f6",
          color: "white",
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
