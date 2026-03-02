import { DispatchOutcome, DispatchPolicy, EventPriority, JourneyTheme } from "@/lib/orchestration/types";

const PRIORITY_WEIGHT: Record<EventPriority, number> = {
  low: 0,
  normal: 1,
  high: 2,
  critical: 3,
};

const DEFAULT_POLICY: DispatchPolicy = {
  immediateMinPriority: "high",
  immediateMinConfidence: 0.8,
  cooldownHours: 8,
  digestSlots: ["09:00", "18:00"],
};

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

function isValidPriority(value: string): value is EventPriority {
  return value === "critical" || value === "high" || value === "normal" || value === "low";
}

export function getDispatchPolicy(): DispatchPolicy {
  const confidence = readOptionalNumber(process.env.MINERVA_IMMEDIATE_MIN_CONFIDENCE);
  const cooldownHours = readOptionalNumber(process.env.MINERVA_TOPIC_COOLDOWN_HOURS);
  const slotsRaw = (process.env.MINERVA_DIGEST_SLOTS ?? "").trim();
  const minPriority = (process.env.MINERVA_IMMEDIATE_MIN_PRIORITY ?? "").trim().toLowerCase() as EventPriority;

  return {
    immediateMinPriority: isValidPriority(minPriority) ? minPriority : DEFAULT_POLICY.immediateMinPriority,
    immediateMinConfidence: confidence !== null ? clampConfidence(confidence) : DEFAULT_POLICY.immediateMinConfidence,
    cooldownHours: cooldownHours !== null && cooldownHours > 0 ? Math.floor(cooldownHours) : DEFAULT_POLICY.cooldownHours,
    digestSlots: slotsRaw
      ? slotsRaw
          .split(",")
          .map((token) => token.trim())
          .filter((token) => token.length > 0)
      : DEFAULT_POLICY.digestSlots,
  };
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
  if (priorityOk && confidenceOk) {
    return {
      decision: "send_now",
      reason: "priority_and_confidence_threshold",
      mode: "immediate",
    };
  }

  return {
    decision: "queue_digest",
    reason: "below_immediate_threshold",
    mode: "digest",
  };
}
