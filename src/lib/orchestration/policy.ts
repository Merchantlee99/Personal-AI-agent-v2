import { BriefingTier, DispatchOutcome, DispatchPolicy, EventPriority, JourneyTheme } from "@/lib/orchestration/types";

const PRIORITY_WEIGHT: Record<EventPriority, number> = {
  low: 0,
  normal: 1,
  high: 2,
  critical: 3,
};

const DEFAULT_POLICY: DispatchPolicy = {
  immediateMinPriority: "high",
  immediateMinConfidence: 0.8,
  immediateMinAlertScore: 78,
  digestMinAlertScore: 55,
  cooldownHours: 8,
  digestSlots: ["09:00", "18:00"],
};

export type AutoClioPolicy = {
  enabled: boolean;
  minImpact: number;
  knowledgeTags: string[];
};

export type AutoClioDecision = {
  shouldRun: boolean;
  reason: string;
};

export type ApprovalPolicy = {
  ttlSec: number;
  frontendEscalationRatio: number;
};

export type TranslationTierPolicy = {
  translateSummary: boolean;
  summaryCharLimit: number;
  maxSnippetTranslations: number;
  snippetCharLimit: number;
};

const DEFAULT_AUTO_CLIO_POLICY: AutoClioPolicy = {
  enabled: true,
  minImpact: 0.75,
  knowledgeTags: ["research", "paper", "analysis", "insight", "whitepaper"],
};

const DEFAULT_APPROVAL_POLICY: ApprovalPolicy = {
  ttlSec: 300,
  frontendEscalationRatio: 0.5,
};

const DEFAULT_TELEGRAM_TRANSLATION_POLICY: Record<BriefingTier, TranslationTierPolicy> = {
  P0: { translateSummary: true, summaryCharLimit: 480, maxSnippetTranslations: 2, snippetCharLimit: 180 },
  P1: { translateSummary: true, summaryCharLimit: 420, maxSnippetTranslations: 1, snippetCharLimit: 160 },
  P2: { translateSummary: false, summaryCharLimit: 0, maxSnippetTranslations: 0, snippetCharLimit: 0 },
};

function parseBoolean(raw: string | undefined, fallback: boolean): boolean {
  if (raw === undefined) {
    return fallback;
  }
  const token = raw.trim().toLowerCase();
  if (token === "1" || token === "true" || token === "yes" || token === "on") {
    return true;
  }
  if (token === "0" || token === "false" || token === "no" || token === "off") {
    return false;
  }
  return fallback;
}

function readOptionalNumber(raw: string | undefined): number | null {
  if (raw === undefined) {
    return null;
  }
  const trimmed = raw.trim();
  if (!trimmed) {
    return null;
  }
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed)) {
    return null;
  }
  return parsed;
}

function clampConfidence(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  if (value < 0) {
    return 0;
  }
  if (value > 1) {
    return 1;
  }
  return value;
}

function clampScore(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  if (value < 0) {
    return 0;
  }
  if (value > 100) {
    return 100;
  }
  return value;
}

function clampRatio(value: number, min = 0, max = 1): number {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.min(max, Math.max(min, value));
}

function isValidPriority(value: string): value is EventPriority {
  return value === "critical" || value === "high" || value === "normal" || value === "low";
}

export function getDispatchPolicy(): DispatchPolicy {
  const confidence = readOptionalNumber(process.env.MINERVA_IMMEDIATE_MIN_CONFIDENCE);
  const immediateMinAlertScore = readOptionalNumber(process.env.MINERVA_IMMEDIATE_MIN_ALERT_SCORE);
  const digestMinAlertScore = readOptionalNumber(process.env.MINERVA_DIGEST_MIN_ALERT_SCORE);
  const cooldownHours = readOptionalNumber(process.env.MINERVA_TOPIC_COOLDOWN_HOURS);
  const slotsRaw = (process.env.MINERVA_DIGEST_SLOTS ?? "").trim();
  const minPriority = (process.env.MINERVA_IMMEDIATE_MIN_PRIORITY ?? "").trim().toLowerCase() as EventPriority;

  return {
    immediateMinPriority: isValidPriority(minPriority) ? minPriority : DEFAULT_POLICY.immediateMinPriority,
    immediateMinConfidence: confidence !== null ? clampConfidence(confidence) : DEFAULT_POLICY.immediateMinConfidence,
    immediateMinAlertScore:
      immediateMinAlertScore !== null ? clampScore(immediateMinAlertScore) : DEFAULT_POLICY.immediateMinAlertScore,
    digestMinAlertScore: digestMinAlertScore !== null ? clampScore(digestMinAlertScore) : DEFAULT_POLICY.digestMinAlertScore,
    cooldownHours: cooldownHours !== null && cooldownHours > 0 ? Math.floor(cooldownHours) : DEFAULT_POLICY.cooldownHours,
    digestSlots: slotsRaw
      ? slotsRaw
          .split(",")
          .map((token) => token.trim())
          .filter((token) => token.length > 0)
      : DEFAULT_POLICY.digestSlots,
  };
}

function parseCsv(raw: string | undefined): string[] {
  if (!raw) {
    return [];
  }
  return raw
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter((item) => item.length > 0);
}

export function getAutoClioPolicy(): AutoClioPolicy {
  const enabled = parseBoolean(process.env.HERMES_AUTO_CLIO_SAVE, DEFAULT_AUTO_CLIO_POLICY.enabled);
  const minImpactRaw = readOptionalNumber(process.env.HERMES_AUTO_CLIO_SAVE_MIN_IMPACT);
  const minImpact = minImpactRaw !== null ? clampRatio(minImpactRaw, 0, 1) : DEFAULT_AUTO_CLIO_POLICY.minImpact;
  const knowledgeTagCandidates = parseCsv(process.env.HERMES_AUTO_CLIO_SAVE_KNOWLEDGE_TAGS);
  const knowledgeTags = knowledgeTagCandidates.length > 0 ? knowledgeTagCandidates : DEFAULT_AUTO_CLIO_POLICY.knowledgeTags;

  return {
    enabled,
    minImpact,
    knowledgeTags,
  };
}

export function evaluateAutoClioPolicy(params: {
  agentId: string;
  priority: EventPriority;
  impactScore?: number;
  tags?: string[];
  policy?: AutoClioPolicy;
}): AutoClioDecision {
  const policy = params.policy ?? getAutoClioPolicy();
  if (!policy.enabled) {
    return { shouldRun: false, reason: "disabled" };
  }
  if (params.agentId !== "hermes") {
    return { shouldRun: false, reason: "agent_not_hermes" };
  }
  if (params.priority === "critical") {
    return { shouldRun: true, reason: "critical_priority" };
  }
  if (params.priority !== "high") {
    return { shouldRun: false, reason: "priority_below_high" };
  }

  const impactScore = Number(params.impactScore ?? 0);
  const tags = new Set((params.tags ?? []).map((token) => token.toLowerCase()));
  const hasKnowledgeTag = policy.knowledgeTags.some((tag) => tags.has(tag));

  if (impactScore >= policy.minImpact || hasKnowledgeTag) {
    return { shouldRun: true, reason: "high_impact_or_knowledge_tag" };
  }
  return { shouldRun: false, reason: "impact_below_threshold" };
}

export function getApprovalPolicy(): ApprovalPolicy {
  const ttlSecRaw = readOptionalNumber(process.env.TELEGRAM_APPROVAL_TTL_SEC);
  const ttlSec = ttlSecRaw !== null ? Math.min(900, Math.max(60, Math.trunc(ttlSecRaw))) : DEFAULT_APPROVAL_POLICY.ttlSec;

  const escalationRaw = readOptionalNumber(process.env.FRONTEND_APPROVAL_ESCALATION_RATIO);
  const frontendEscalationRatio =
    escalationRaw !== null ? clampRatio(escalationRaw, 0.1, 0.95) : DEFAULT_APPROVAL_POLICY.frontendEscalationRatio;

  return {
    ttlSec,
    frontendEscalationRatio,
  };
}

export function resolveApprovalEscalationRatio(rawInput?: string | null): number {
  const fallback = getApprovalPolicy().frontendEscalationRatio;
  if (rawInput === null || rawInput === undefined) {
    return fallback;
  }
  const parsed = Number(rawInput.trim());
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return clampRatio(parsed, 0, 0.95);
}

function parseInteger(raw: string | undefined, fallback: number, minValue: number, maxValue: number): number {
  const parsed = readOptionalNumber(raw);
  if (parsed === null) {
    return fallback;
  }
  return Math.min(maxValue, Math.max(minValue, Math.trunc(parsed)));
}

function parseTierTranslationPolicy(tier: BriefingTier): TranslationTierPolicy {
  const defaults = DEFAULT_TELEGRAM_TRANSLATION_POLICY[tier];
  const prefix = `TELEGRAM_TRANSLATE_${tier}`;
  return {
    translateSummary: parseBoolean(process.env[`${prefix}_SUMMARY`], defaults.translateSummary),
    summaryCharLimit: parseInteger(process.env[`${prefix}_SUMMARY_CHAR_LIMIT`], defaults.summaryCharLimit, 0, 2000),
    maxSnippetTranslations: parseInteger(process.env[`${prefix}_SNIPPETS_MAX`], defaults.maxSnippetTranslations, 0, 10),
    snippetCharLimit: parseInteger(process.env[`${prefix}_SNIPPET_CHAR_LIMIT`], defaults.snippetCharLimit, 0, 500),
  };
}

export function getTelegramTranslationPolicy(tier: BriefingTier): TranslationTierPolicy {
  return parseTierTranslationPolicy(tier);
}

function inferJourneyTheme(now: Date): JourneyTheme {
  const hour = now.getHours();
  if (hour >= 5 && hour < 12) {
    return "morning_briefing";
  }
  if (hour >= 16 && hour < 23) {
    return "evening_wrapup";
  }
  return "adhoc";
}

export function getJourneyTheme(now = new Date()): JourneyTheme {
  return inferJourneyTheme(now);
}

export function evaluateDispatchPolicy(params: {
  priority: EventPriority;
  confidence: number;
  alertScore: number;
  policy: DispatchPolicy;
  cooldownUntil?: string | null;
  now?: Date;
}): DispatchOutcome {
  const now = params.now ?? new Date();
  const cooldownUntil = params.cooldownUntil ? new Date(params.cooldownUntil) : null;

  if (cooldownUntil && Number.isFinite(cooldownUntil.getTime()) && cooldownUntil.getTime() > now.getTime()) {
    return {
      decision: "suppressed_cooldown",
      reason: "topic_cooldown_active",
      mode: "digest",
      cooldownUntil: cooldownUntil.toISOString(),
    };
  }

  const priorityOk = PRIORITY_WEIGHT[params.priority] >= PRIORITY_WEIGHT[params.policy.immediateMinPriority];
  const confidenceOk = params.confidence >= params.policy.immediateMinConfidence;
  const immediateScoreOk = params.alertScore >= params.policy.immediateMinAlertScore;
  if (priorityOk && confidenceOk && immediateScoreOk) {
    return {
      decision: "send_now",
      reason: "priority_confidence_score_threshold",
      mode: "immediate",
    };
  }

  if (params.alertScore < params.policy.digestMinAlertScore) {
    return {
      decision: "queue_digest",
      reason: "low_signal_score",
      mode: "digest",
    };
  }

  return {
    decision: "queue_digest",
    reason: "below_immediate_threshold",
    mode: "digest",
  };
}
