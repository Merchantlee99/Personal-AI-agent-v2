import type { RefObject } from "react";
import { AGENTS, CanonicalAgentId } from "@/lib/agents";
import { motion } from "framer-motion";
import type { DashboardTheme, ThemeColors } from "./theme";
import type { ChatMessage } from "./types";
import { AgentVisual } from "./agent-visual";

type HistoryPanelProps = {
  agentId: CanonicalAgentId;
  messages: ChatMessage[];
  isSending: boolean;
  error: string | null;
  theme: DashboardTheme;
  colors: ThemeColors;
  bottomRef?: RefObject<HTMLDivElement | null>;
};

export function HistoryPanel({ agentId, messages, isSending, error, theme, colors, bottomRef }: HistoryPanelProps) {
  const agent = AGENTS[agentId];
  const rootStyle = { flex: 1, overflowY: "auto" as const, padding: "18px 24px" };
  const columnStyle = { maxWidth: "760px", margin: "0 auto", display: "flex", flexDirection: "column" as const, gap: "18px" };
  const rowStyle = { display: "flex", gap: "12px" };
  const messageBodyStyle = { minWidth: 0 };
  const assistantAvatarBg = `rgba(${agent.color.r}, ${agent.color.g}, ${agent.color.b}, ${theme === "dark" ? 0.12 : 0.08})`;
  const assistantAvatarStyle = {
    width: "28px",
    height: "28px",
    borderRadius: "8px",
    border: `1px solid ${colors.border}`,
    background: assistantAvatarBg,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
    marginTop: "2px",
  };
  const typingAvatarStyle = {
    width: "28px",
    height: "28px",
    borderRadius: "8px",
    border: `1px solid ${colors.border}`,
    background: assistantAvatarBg,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
  };
  const userBadgeTextStyle = { color: colors.textMuted, fontSize: "12px" };
  const messageMetaStyle = { color: colors.textSecondary, fontSize: "13px", marginBottom: "4px", fontWeight: 500 };
  const messageTextStyle = { color: colors.textPrimary, fontSize: "14px", whiteSpace: "pre-wrap" as const, lineHeight: 1.6 };
  const typingTextStyle = { color: colors.textFaint, fontSize: "13px", display: "flex", alignItems: "center" };
  const errorStyle = {
    border: "1px solid rgba(239,68,68,0.35)",
    background: "rgba(239,68,68,0.1)",
    color: "#FCA5A5",
    borderRadius: "8px",
    padding: "10px 12px",
    fontSize: "13px",
  };

  return (
    <div style={rootStyle}>
      <div style={columnStyle}>
        {messages.map((message, index) => (
          <motion.div
            key={`${message.at}-${index}`}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, ease: "easeOut" }}
            style={rowStyle}
          >
            <div style={message.role === "assistant" ? assistantAvatarStyle : { ...assistantAvatarStyle, background: colors.surface }}>
              {message.role === "assistant" ? (
                <AgentVisual agentId={agentId} state="idle" theme={theme} size="mini" />
              ) : (
                <span style={userBadgeTextStyle}>상</span>
              )}
            </div>
            <div style={messageBodyStyle}>
              <div style={messageMetaStyle}>
                {message.role === "assistant" ? agent.name : "상인"}
              </div>
              <div style={messageTextStyle}>
                {message.text}
              </div>
            </div>
          </motion.div>
        ))}

        {isSending && (
          <div style={rowStyle}>
            <div style={typingAvatarStyle}>
              <AgentVisual agentId={agentId} state="thinking" theme={theme} size="mini" />
            </div>
            <div style={typingTextStyle}>
              생각 중...
            </div>
          </div>
        )}

        {error && (
          <div style={errorStyle}>
            {error}
          </div>
        )}

        {bottomRef ? <div ref={bottomRef} /> : null}
      </div>
    </div>
  );
}
