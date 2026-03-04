import { CanonicalAgentId } from "@/lib/agents";

export type EventPriority = "critical" | "high" | "normal" | "low";
export type DispatchMode = "immediate" | "digest";
export type DispatchDecision = "send_now" | "queue_digest" | "suppressed_cooldown";
export type JourneyTheme = "morning_briefing" | "evening_wrapup" | "adhoc";

export type SourceRef = {
  title: string;
  url: string;
  snippet?: string;
  publisher?: string;
  publishedAt?: string;
  category?: string;
  priorityTier?: "P0" | "P1" | "P2";
  domain?: string;
};

export type AgentEventInput = {
  agentId: CanonicalAgentId;
  topicKey: string;
  title: string;
  summary: string;
  priority: EventPriority;
  confidence: number;
  tags?: string[];
  sourceRefs?: SourceRef[];
  impactScore?: number;
  insightHint?: string;
  payload?: Record<string, unknown>;
};

export type AgentEvent = AgentEventInput & {
  eventId: string;
  createdAt: string;
  theme: JourneyTheme;
  dedupeKey: string;
};

export type DispatchPolicy = {
  immediateMinPriority: EventPriority;
  immediateMinConfidence: number;
  cooldownHours: number;
  digestSlots: string[];
};

export type DispatchOutcome = {
  decision: DispatchDecision;
  reason: string;
  mode: DispatchMode;
  cooldownUntil?: string;
};

export type MinervaCalendarBriefItem = {
  timeLabel: string;
  title: string;
};

export type MinervaCalendarBriefing = {
  summary: string;
  items: MinervaCalendarBriefItem[];
};

export type TelegramInlineButton = { text: string; callback_data: string };
export type TelegramInlineKeyboard = { inline_keyboard: TelegramInlineButton[][] };

export type TelegramDispatchPayload = {
  chat_id: string;
  text: string;
  disable_web_page_preview?: boolean;
  reply_markup?: TelegramInlineKeyboard;
};
