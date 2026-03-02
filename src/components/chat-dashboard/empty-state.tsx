import { AGENTS, CanonicalAgentId } from "@/lib/agents";
import { motion, AnimatePresence } from "framer-motion";
import type { DashboardTheme, ThemeColors } from "./theme";
import { QUICK_COMMANDS } from "./quick-commands";
import { AgentVisual } from "./agent-visual";

type EmptyStateProps = {
  agentId: CanonicalAgentId;
  theme: DashboardTheme;
  colors: ThemeColors;
  onQuickCommand: (message: string) => void;
};

export function EmptyState({ agentId, theme, colors, onQuickCommand }: EmptyStateProps) {
  const agent = AGENTS[agentId];

  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "20px",
        gap: "16px",
      }}
    >
      <AnimatePresence mode="wait">
        <motion.div
          key={agentId}
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.95 }}
          transition={{ duration: 0.25 }}
          style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "16px" }}
        >
          <AgentVisual agentId={agentId} state="idle" theme={theme} size="lg" />

          <div style={{ textAlign: "center" }}>
            <h2 style={{ margin: 0, color: colors.textPrimary, fontSize: "20px", fontWeight: 600 }}>{agent.name}</h2>
            <p style={{ margin: 0, color: colors.textMuted, fontSize: "14px" }}>{agent.greeting}</p>
          </div>
        </motion.div>
      </AnimatePresence>

      <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", maxWidth: "560px", justifyContent: "center" }}>
        {QUICK_COMMANDS[agentId].map((command, i) => (
          <motion.button
            key={command.label}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25, delay: i * 0.08 }}
            onClick={() => onQuickCommand(command.message)}
            style={{
              background: colors.surface,
              border: `1px solid ${colors.border}`,
              borderRadius: "8px",
              padding: "8px 12px",
              color: colors.textSecondary,
              fontSize: "13px",
              cursor: "pointer",
            }}
          >
            {command.label}
          </motion.button>
        ))}
      </div>
    </div>
  );
}
