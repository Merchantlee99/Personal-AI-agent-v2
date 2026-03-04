export const ORCHESTRATION_EVENT_SCHEMA_VERSION = "1.0";

const CANONICAL_AGENT_IDS = new Set(["minerva", "clio", "hermes"]);
const EVENT_PRIORITIES = new Set(["critical", "high", "normal", "low"]);
const PRIORITY_TIERS = new Set(["P0", "P1", "P2"]);

export type OrchestrationEventContractMeta = {
  schemaVersion: string;
  eventType: string;
  producer: string;
  occurredAt: string;
  traceId?: string;
  legacy: boolean;
};

export type ContractValidationError = {
  path: string;
  message: string;
};

type ParseSuccess = {
  ok: true;
  payload: Record<string, unknown>;
  contract: OrchestrationEventContractMeta;
};

type ParseFailure = {
  ok: false;
  error: string;
  detail?: string;
  errors?: ContractValidationError[];
};

function asRecord(input: unknown): Record<string, unknown> | null {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    return null;
  }
  return input as Record<string, unknown>;
}

function isIsoDate(value: string) {
  const time = new Date(value).getTime();
  return Number.isFinite(time);
}

function isHttpUrl(value: string) {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

function normalizeString(value: unknown) {
  if (typeof value !== "string") {
    return "";
  }
  return value.trim();
}

function parseBoolEnv(name: string, fallback: boolean) {
  const raw = process.env[name];
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

function pushError(errors: ContractValidationError[], path: string, message: string) {
  errors.push({ path, message });
}

function validateSourceRef(item: unknown, path: string, errors: ContractValidationError[]) {
  const obj = asRecord(item);
  if (!obj) {
    pushError(errors, path, "must be an object");
    return;
  }
  const title = normalizeString(obj.title);
  const url = normalizeString(obj.url);
  if (!title) {
    pushError(errors, `${path}.title`, "required non-empty string");
  }
  if (!url) {
    pushError(errors, `${path}.url`, "required non-empty string");
  } else if (!isHttpUrl(url)) {
    pushError(errors, `${path}.url`, "must be a valid http/https URL");
  }

  if (obj.snippet !== undefined && typeof obj.snippet !== "string") {
    pushError(errors, `${path}.snippet`, "must be string");
  }
  if (obj.publisher !== undefined && typeof obj.publisher !== "string") {
    pushError(errors, `${path}.publisher`, "must be string");
  }
  if (obj.publishedAt !== undefined) {
    if (typeof obj.publishedAt !== "string" || !isIsoDate(obj.publishedAt)) {
      pushError(errors, `${path}.publishedAt`, "must be ISO-8601 datetime string");
    }
  }
  if (obj.category !== undefined && typeof obj.category !== "string") {
    pushError(errors, `${path}.category`, "must be string");
  }
  if (obj.priorityTier !== undefined) {
    const tier = normalizeString(obj.priorityTier);
    if (!PRIORITY_TIERS.has(tier)) {
      pushError(errors, `${path}.priorityTier`, "must be one of P0/P1/P2");
    }
  }
  if (obj.domain !== undefined && typeof obj.domain !== "string") {
    pushError(errors, `${path}.domain`, "must be string");
  }
}

function validatePayload(payload: unknown, errors: ContractValidationError[]) {
  const obj = asRecord(payload);
  if (!obj) {
    pushError(errors, "$.payload", "must be object");
    return;
  }

  const agentId = normalizeString(obj.agentId).toLowerCase();
  if (!CANONICAL_AGENT_IDS.has(agentId)) {
    pushError(errors, "$.payload.agentId", "must be one of minerva/clio/hermes");
  }
  const topicKey = normalizeString(obj.topicKey);
  if (!topicKey) {
    pushError(errors, "$.payload.topicKey", "required non-empty string");
  }
  const title = normalizeString(obj.title);
  if (!title) {
    pushError(errors, "$.payload.title", "required non-empty string");
  }
  const summary = normalizeString(obj.summary);
  if (!summary) {
    pushError(errors, "$.payload.summary", "required non-empty string");
  }

  const priority = normalizeString(obj.priority).toLowerCase();
  if (!EVENT_PRIORITIES.has(priority)) {
    pushError(errors, "$.payload.priority", "must be one of critical/high/normal/low");
  }

  if (typeof obj.confidence !== "number" || !Number.isFinite(obj.confidence) || obj.confidence < 0 || obj.confidence > 1) {
    pushError(errors, "$.payload.confidence", "must be number within [0,1]");
  }

  if (obj.tags !== undefined) {
    if (!Array.isArray(obj.tags) || obj.tags.some((item) => typeof item !== "string")) {
      pushError(errors, "$.payload.tags", "must be string[]");
    }
  }
  if (obj.sourceRefs !== undefined) {
    if (!Array.isArray(obj.sourceRefs)) {
      pushError(errors, "$.payload.sourceRefs", "must be array");
    } else {
      obj.sourceRefs.forEach((item, index) => validateSourceRef(item, `$.payload.sourceRefs[${index}]`, errors));
    }
  }
  if (obj.impactScore !== undefined && (typeof obj.impactScore !== "number" || !Number.isFinite(obj.impactScore))) {
    pushError(errors, "$.payload.impactScore", "must be number");
  }
  if (obj.insightHint !== undefined && typeof obj.insightHint !== "string") {
    pushError(errors, "$.payload.insightHint", "must be string");
  }
  if (obj.payload !== undefined && !asRecord(obj.payload)) {
    pushError(errors, "$.payload.payload", "must be object");
  }
  if (obj.chatId !== undefined && typeof obj.chatId !== "string") {
    pushError(errors, "$.payload.chatId", "must be string");
  }
  if (obj.forceDispatch !== undefined && typeof obj.forceDispatch !== "boolean") {
    pushError(errors, "$.payload.forceDispatch", "must be boolean");
  }
}

function validateV1Envelope(raw: Record<string, unknown>): ContractValidationError[] {
  const errors: ContractValidationError[] = [];
  const eventType = normalizeString(raw.eventType);
  const producer = normalizeString(raw.producer);
  const occurredAt = normalizeString(raw.occurredAt);
  const payload = raw.payload;

  if (!eventType) {
    pushError(errors, "$.eventType", "required non-empty string");
  }
  if (!producer) {
    pushError(errors, "$.producer", "required non-empty string");
  }
  if (!occurredAt) {
    pushError(errors, "$.occurredAt", "required ISO-8601 datetime string");
  } else if (!isIsoDate(occurredAt)) {
    pushError(errors, "$.occurredAt", "must be valid ISO-8601 datetime string");
  }
  if (raw.traceId !== undefined && typeof raw.traceId !== "string") {
    pushError(errors, "$.traceId", "must be string");
  }

  validatePayload(payload, errors);
  return errors;
}

export function parseOrchestrationEventContract(raw: unknown): ParseSuccess | ParseFailure {
  const obj = asRecord(raw);
  if (!obj) {
    return { ok: false, error: "invalid_event_contract", detail: "payload must be an object" };
  }

  const requireSchemaV1 = parseBoolEnv("ORCH_REQUIRE_SCHEMA_V1", false);
  const schemaVersion = normalizeString(obj.schemaVersion);
  if (!schemaVersion) {
    if (requireSchemaV1) {
      return {
        ok: false,
        error: "schema_version_required",
        detail: "ORCH_REQUIRE_SCHEMA_V1=true requires schemaVersion=1.0 envelope",
        errors: [{ path: "$.schemaVersion", message: "required when ORCH_REQUIRE_SCHEMA_V1=true" }],
      };
    }
    return {
      ok: true,
      payload: obj,
      contract: {
        schemaVersion: "legacy",
        eventType: "agent.event.legacy",
        producer: "legacy-client",
        occurredAt: new Date().toISOString(),
        legacy: true,
      },
    };
  }

  if (schemaVersion !== ORCHESTRATION_EVENT_SCHEMA_VERSION) {
    return {
      ok: false,
      error: "unsupported_schema_version",
      detail: `supported=${ORCHESTRATION_EVENT_SCHEMA_VERSION}, received=${schemaVersion}`,
    };
  }

  const errors = validateV1Envelope(obj);
  if (errors.length > 0) {
    return {
      ok: false,
      error: "invalid_event_contract",
      detail: `v1 validation failed (${errors.length} issue${errors.length > 1 ? "s" : ""})`,
      errors,
    };
  }

  const eventType = normalizeString(obj.eventType);
  const producer = normalizeString(obj.producer);
  const occurredAt = normalizeString(obj.occurredAt);
  const traceId = normalizeString(obj.traceId);
  const payload = asRecord(obj.payload)!;

  return {
    ok: true,
    payload,
    contract: {
      schemaVersion,
      eventType,
      producer,
      occurredAt,
      traceId: traceId || undefined,
      legacy: false,
    },
  };
}
