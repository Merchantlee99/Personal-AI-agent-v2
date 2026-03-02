import type { CanonicalAgentId } from "@/lib/agents";

export type AgentState = "idle" | "listening" | "thinking" | "speaking";

export type ChatMessage = {
  role: "user" | "assistant";
  text: string;
  at: string;
};

export type HistoryState = Record<CanonicalAgentId, ChatMessage[]>;

export const createEmptyHistory = (): HistoryState => ({
  minerva: [],
  clio: [],
  hermes: [],
});
