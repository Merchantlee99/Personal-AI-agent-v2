import { CSSProperties, KeyboardEvent } from "react";
import { AGENTS, CanonicalAgentId } from "@/lib/agents";
import type { ThemeColors } from "./theme";
import type { AgentState } from "./types";
import { AgentVisual } from "./agent-visual";

type ComposerProps = {
  agentId: CanonicalAgentId;
  agentState: AgentState;
  inputText: string;
  isSending: boolean;
  isFocused: boolean;
  colors: ThemeColors;
  onFocus: () => void;
  onBlur: () => void;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
};

export function Composer({
  agentId,
  agentState,
  inputText,
  isSending,
  isFocused,
  colors,
  onFocus,
  onBlur,
  onChange,
  onSubmit,
  onKeyDown,
}: ComposerProps) {
  const agent = AGENTS[agentId];
  const wrapperStyle: CSSProperties = {
    borderTop: `1px solid ${colors.border}`,
    padding: "12px 24px 20px 24px",
    background: colors.bg,
  };
  const contentStyle: CSSProperties = { maxWidth: "760px", margin: "0 auto" };
  const inputShellStyle: CSSProperties = {
    position: "relative",
    background: colors.bgSecondary,
    border: `1px solid ${isFocused ? colors.borderHover : colors.border}`,
    borderRadius: "12px",
  };
  const textareaStyle: CSSProperties = {
    width: "100%",
    minHeight: "24px",
    maxHeight: "160px",
    resize: "none",
    border: "none",
    background: "transparent",
    color: colors.textPrimary,
    outline: "none",
    fontSize: "14px",
    lineHeight: 1.5,
    padding: "12px 48px 12px 14px",
    boxSizing: "border-box",
    fontFamily: "inherit",
  };
  const sendButtonStyle: CSSProperties = {
    position: "absolute",
    right: "8px",
    bottom: "8px",
    width: "32px",
    height: "32px",
    borderRadius: "8px",
    border: "none",
    cursor: inputText.trim() && !isSending ? "pointer" : "default",
    background: inputText.trim() ? colors.sendButton : colors.sendDisabled,
    color: inputText.trim() && !isSending ? colors.sendIcon : colors.textFaint,
    fontSize: "14px",
    fontWeight: 700,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  };
  const footerHintStyle: CSSProperties = { marginTop: "6px", textAlign: "center", fontSize: "11px", color: colors.textFaint };

  return (
    <div style={wrapperStyle}>
      <div style={contentStyle}>
        <div style={inputShellStyle}>
          <textarea
            value={inputText}
            onChange={(event) => onChange(event.target.value)}
            onFocus={onFocus}
            onBlur={onBlur}
            onKeyDown={onKeyDown}
            placeholder={`${agent.name}에게 메시지를 입력하세요...`}
            rows={Math.min(5, inputText.split("\n").length || 1)}
            disabled={isSending}
            style={textareaStyle}
          />

          <button
            onClick={onSubmit}
            disabled={!inputText.trim() || isSending}
            style={sendButtonStyle}
          >
            {isSending ? (
              <AgentVisual agentId={agentId} state={agentState} theme="dark" size="mini" />
            ) : "↑"}
          </button>
        </div>

        <div style={footerHintStyle}>
          Enter 전송 · Shift+Enter 줄바꿈
        </div>
      </div>
    </div>
  );
}
