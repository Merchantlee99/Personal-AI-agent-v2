import { AGENTS, CANONICAL_AGENT_IDS, CanonicalAgentId } from "@/lib/agents";
import type { ThemeColors, DashboardTheme } from "./theme";
import type { AgentState } from "./types";
import { AgentVisual } from "./agent-visual";

type SidebarProps = {
  activeAgent: CanonicalAgentId;
  agentState: AgentState;
  onSelectAgent: (agentId: CanonicalAgentId) => void;
  theme: DashboardTheme;
  colors: ThemeColors;
  onToggleTheme: () => void;
};

export function Sidebar({ activeAgent, agentState, onSelectAgent, theme, colors, onToggleTheme }: SidebarProps) {
  return (
    <aside
      style={{
        width: "68px",
        borderRight: `1px solid ${colors.border}`,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: "10px",
        padding: "16px 0",
      }}
    >
      <div
        style={{
          width: "40px",
          height: "40px",
          borderRadius: "12px",
          border: `1px solid ${colors.border}`,
          background: colors.surface,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: colors.textPrimary,
          fontSize: "16px",
          marginBottom: "8px",
        }}
      >
        N
      </div>

      {CANONICAL_AGENT_IDS.map((agentId) => {
        const isSelected = agentId === activeAgent;
        const agent = AGENTS[agentId];
        return (
          <button
            key={agentId}
            onClick={() => onSelectAgent(agentId)}
            title={`${agent.name} (${agent.role})`}
            style={{
              width: "40px",
              height: "40px",
              borderRadius: "10px",
              border: `1px solid ${isSelected ? agent.color.glow : colors.border}`,
              background: isSelected ? colors.surfaceHover : "transparent",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              cursor: "pointer",
            }}
          >
            <div style={{ display: 'flex', position: 'relative', width: "24px", height: "24px" }}>
              <AgentVisual
                agentId={agentId}
                state={isSelected ? agentState : "idle"}
                theme={theme}
                size="mini"
              />
              {/* Online indicator dot for active sidebar item */}
              {isSelected && (
                <div style={{
                  position: "absolute",
                  right: "-2px",
                  bottom: "-2px",
                  width: "8px",
                  height: "8px",
                  borderRadius: "50%",
                  background:
                    agentState === "thinking" ? agent.color.glow :
                      agentState === "working" ? agent.color.glow :
                        agentState === "warning" ? "#EF4444" :
                        "#22C55E",
                  animation:
                    agentState === "thinking" ? "dot-fast-blink 0.6s ease-in-out infinite" :
                      agentState === "working" ? "dot-speak-pulse 0.4s ease-in-out infinite alternate" :
                        agentState === "warning" ? "dot-fast-blink 0.4s ease-in-out infinite" :
                        "none",
                  boxShadow:
                    agentState === "thinking" ? `0 0 6px ${agent.color.glow}` :
                      agentState === "working" ? `0 0 8px ${agent.color.glow}` :
                        agentState === "warning" ? "0 0 10px #EF4444" :
                        "none",
                }} />
              )}
            </div>
          </button>
        );
      })}

      <div style={{ marginTop: "auto" }}>
        <button
          onClick={onToggleTheme}
          style={{
            width: "40px",
            height: "40px",
            borderRadius: "10px",
            border: `1px solid ${colors.border}`,
            background: colors.surface,
            color: colors.textSecondary,
            cursor: "pointer",
            fontSize: "16px",
          }}
        >
          {theme === "dark" ? "◐" : "◑"}
        </button>
      </div>
    </aside >
  );
}
