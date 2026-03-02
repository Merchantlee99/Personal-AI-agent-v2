import agentConfig from "../../config/agents.json";

export type CanonicalAgentId = "minerva" | "clio" | "hermes";
export const CANONICAL_AGENT_IDS = agentConfig.canonical_ids as readonly CanonicalAgentId[];

type AgentVisual = {
  displayName: string;
  greeting: string;
  color: { main: string; glow: string; secondary: string; r: number; g: number; b: number };
};

type AgentConfigRecord = Record<CanonicalAgentId, { display_name: string; role: string }>;
const AGENT_CONFIG = agentConfig.agents as AgentConfigRecord;

const AGENT_VISUALS: Record<CanonicalAgentId, AgentVisual> = {
  minerva: {
    displayName: "미네르바",
    greeting: "무엇을 도와드릴까요?",
    color: { main: "#4338CA", glow: "#6366F1", secondary: "#818CF8", r: 99, g: 102, b: 241 },
  },
  clio: {
    displayName: "클리오",
    greeting: "어떤 지식을 정리해드릴까요?",
    color: { main: "#EA580C", glow: "#F97316", secondary: "#FB923C", r: 249, g: 115, b: 22 },
  },
  hermes: {
    displayName: "헤르메스",
    greeting: "어떤 트렌드를 조사할까요?",
    color: { main: "#059669", glow: "#10B981", secondary: "#34D399", r: 16, g: 185, b: 129 },
  },
};

export const AGENTS: Record<
  CanonicalAgentId,
  {
    id: CanonicalAgentId;
    name: string;
    displayName: string;
    role: string;
    color: { main: string; glow: string; secondary: string; r: number; g: number; b: number };
    greeting: string;
  }
> = CANONICAL_AGENT_IDS.reduce(
  (acc, id) => {
    const configEntry = AGENT_CONFIG[id];
    const visualEntry = AGENT_VISUALS[id];
    acc[id] = {
      id,
      name: configEntry.display_name,
      role: configEntry.role,
      displayName: visualEntry.displayName,
      greeting: visualEntry.greeting,
      color: visualEntry.color,
    };
    return acc;
  },
  {} as Record<CanonicalAgentId, {
    id: CanonicalAgentId;
    name: string;
    displayName: string;
    role: string;
    color: { main: string; glow: string; secondary: string; r: number; g: number; b: number };
    greeting: string;
  }>
);

export const AGENT_LABELS: Record<CanonicalAgentId, string> = CANONICAL_AGENT_IDS.reduce(
  (acc, id) => {
    acc[id] = `${AGENTS[id].name} · ${AGENTS[id].displayName}`;
    return acc;
  },
  {} as Record<CanonicalAgentId, string>
);

export const normalizeAgentId = (value: string): CanonicalAgentId | null => {
  const trimmed = value.toLowerCase().trim();
  if ((CANONICAL_AGENT_IDS as readonly string[]).includes(trimmed)) {
    return trimmed as CanonicalAgentId;
  }
  return null;
};
