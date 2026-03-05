import { EventPriority } from "@/lib/orchestration/types";

export const ORCHESTRATION_EVENT_SCHEMA_VERSION = 1 as const;

export type EventContractV1SourceRef = {
  title: string;
  url: string;
  snippet?: string;
  publisher?: string;
  publishedAt?: string;
  category?: string;
  priorityTier?: "P0" | "P1" | "P2";
  domain?: string;
};

export type EventContractV1Payload = {
  schemaVersion: number;
  agentId: string;
  topicKey: string;
  title: string;
  summary: string;
  priority: EventPriority;
  confidence: number;
  tags?: string[];
  sourceRefs?: EventContractV1SourceRef[];
  impactScore?: number;
  insightHint?: string;
  payload?: Record<string, unknown>;
  chatId?: string;
  forceDispatch?: boolean;
};

export type EventContractValidationResult =
  | {
      ok: true;
      payload: EventContractV1Payload;
      mode: "strict_v1" | "legacy_defaulted_v1";
    }
  | {
      ok: false;
      error: "invalid_event_contract";
      mode: "strict_v1" | "legacy_defaulted_v1";
      issues: string[];
      required: string[];
      schemaVersion: number;
    };

const PRIORITY_VALUES: readonly EventPriority[] = ["critical", "high", "normal", "low"] as const;
const PRIORITY_TIER_VALUES = new Set(["P0", "P1", "P2"]);

const REQUIRED_FIELDS = ["agentId", "topicKey", "title", "summary", "priority", "confidence"];

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function compact(value: string): string {
  return value.replace(/\s+/g, " ").trim();
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

function looksLikeHttpUrl(value: string): boolean {
  return /^https?:\/\//i.test(value.trim());
}

function normalizeSourceRef(input: unknown, index: number, issues: string[]): EventContractV1SourceRef | null {
  if (!isPlainObject(input)) {
    issues.push(`sourceRefs[${index}] must be object`);
    return null;
  }
  const title = compact(asString(input.title));
  const url = compact(asString(input.url));
  if (!title) {
    issues.push(`sourceRefs[${index}].title is required`);
  }
  if (!url) {
    issues.push(`sourceRefs[${index}].url is required`);
  } else if (!looksLikeHttpUrl(url)) {
    issues.push(`sourceRefs[${index}].url must start with http/https`);
  }
  if (!title || !url) {
    return null;
  }

  const priorityTierRaw = compact(asString(input.priorityTier)).toUpperCase();
  const priorityTier = PRIORITY_TIER_VALUES.has(priorityTierRaw) ? (priorityTierRaw as "P0" | "P1" | "P2") : undefined;
  if (priorityTierRaw && !priorityTier) {
    issues.push(`sourceRefs[${index}].priorityTier must be one of P0/P1/P2`);
  }

  return {
    title,
    url,
    snippet: compact(asString(input.snippet)) || undefined,
    publisher: compact(asString(input.publisher)) || undefined,
    publishedAt: compact(asString(input.publishedAt)) || undefined,
    category: compact(asString(input.category)) || undefined,
    priorityTier,
    domain: compact(asString(input.domain)) || undefined,
  };
}

export function validateEventContractV1(
  rawBody: unknown,
  opts?: {
    requireExplicitSchemaVersion?: boolean;
  }
): EventContractValidationResult {
  const requireExplicit = Boolean(opts?.requireExplicitSchemaVersion);
  const mode: "strict_v1" | "legacy_defaulted_v1" = requireExplicit ? "strict_v1" : "legacy_defaulted_v1";
  const issues: string[] = [];

  if (!isPlainObject(rawBody)) {
    return {
      ok: false,
      error: "invalid_event_contract",
      mode,
      schemaVersion: ORCHESTRATION_EVENT_SCHEMA_VERSION,
      issues: ["request body must be a JSON object"],
      required: REQUIRED_FIELDS,
    };
  }

  const hasSchemaVersion = rawBody.schemaVersion !== undefined || rawBody.schema_version !== undefined;
  const schemaVersionRaw = hasSchemaVersion ? rawBody.schemaVersion ?? rawBody.schema_version : ORCHESTRATION_EVENT_SCHEMA_VERSION;
  const schemaVersionNum = Number(schemaVersionRaw);

  if (requireExplicit && !hasSchemaVersion) {
    issues.push("schemaVersion is required when ORCH_REQUIRE_SCHEMA_V1=true");
  }
  if (!Number.isFinite(schemaVersionNum) || Math.trunc(schemaVersionNum) !== ORCHESTRATION_EVENT_SCHEMA_VERSION) {
    issues.push(`unsupported schemaVersion: ${String(schemaVersionRaw)} (supported: 1)`);
  }

  const agentId = compact(asString(rawBody.agentId));
  const topicKey = compact(asString(rawBody.topicKey));
  const title = compact(asString(rawBody.title));
  const summary = compact(asString(rawBody.summary));

  const priorityRaw = compact(asString(rawBody.priority)).toLowerCase();
  const priority = PRIORITY_VALUES.includes(priorityRaw as EventPriority) ? (priorityRaw as EventPriority) : null;

  const confidenceRaw = Number(rawBody.confidence);
  const confidence = clampConfidence(confidenceRaw);

  if (!agentId) {
    issues.push("agentId is required");
  }
  if (!topicKey) {
    issues.push("topicKey is required");
  }
  if (!title) {
    issues.push("title is required");
  }
  if (!summary) {
    issues.push("summary is required");
  }
  if (!priority) {
    issues.push("priority must be one of critical/high/normal/low");
  }
  if (!Number.isFinite(confidenceRaw)) {
    issues.push("confidence must be a finite number");
  }

  let tags: string[] | undefined;
  if (rawBody.tags !== undefined) {
    if (!Array.isArray(rawBody.tags)) {
      issues.push("tags must be an array of strings");
    } else {
      tags = rawBody.tags
        .map((item) => compact(asString(item)))
        .filter((item) => item.length > 0)
        .slice(0, 24);
    }
  }

  let sourceRefs: EventContractV1SourceRef[] | undefined;
  if (rawBody.sourceRefs !== undefined) {
    if (!Array.isArray(rawBody.sourceRefs)) {
      issues.push("sourceRefs must be an array");
    } else {
      sourceRefs = rawBody.sourceRefs
        .map((entry, index) => normalizeSourceRef(entry, index, issues))
        .filter((entry): entry is EventContractV1SourceRef => entry !== null)
        .slice(0, 12);
    }
  }

  let impactScore: number | undefined;
  if (rawBody.impactScore !== undefined) {
    const value = Number(rawBody.impactScore);
    if (!Number.isFinite(value)) {
      issues.push("impactScore must be a finite number");
    } else if (value < 0 || value > 1) {
      issues.push("impactScore must be between 0 and 1");
    } else {
      impactScore = value;
    }
  }

  let payload: Record<string, unknown> | undefined;
  if (rawBody.payload !== undefined) {
    if (!isPlainObject(rawBody.payload)) {
      issues.push("payload must be a JSON object");
    } else {
      payload = rawBody.payload;
    }
  }

  const insightHint = compact(asString(rawBody.insightHint)) || undefined;
  const chatId = compact(asString(rawBody.chatId)) || undefined;
  const forceDispatch = rawBody.forceDispatch === undefined ? undefined : Boolean(rawBody.forceDispatch);

  if (issues.length > 0 || !priority) {
    return {
      ok: false,
      error: "invalid_event_contract",
      mode,
      schemaVersion: ORCHESTRATION_EVENT_SCHEMA_VERSION,
      issues,
      required: REQUIRED_FIELDS,
    };
  }

  return {
    ok: true,
    mode: hasSchemaVersion ? "strict_v1" : "legacy_defaulted_v1",
    payload: {
      schemaVersion: ORCHESTRATION_EVENT_SCHEMA_VERSION,
      agentId,
      topicKey,
      title,
      summary,
      priority,
      confidence,
      tags,
      sourceRefs,
      impactScore,
      insightHint,
      payload,
      chatId,
      forceDispatch,
    },
  };
}
