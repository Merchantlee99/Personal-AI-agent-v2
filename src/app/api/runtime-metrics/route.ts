import fs from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";
import { listAgentEvents, getApprovalQueueStats } from "@/lib/orchestration/storage";

type JsonObject = Record<string, unknown>;

const ROOT = process.env.SHARED_ROOT_PATH?.trim() || path.join(process.cwd(), "shared_data");
const LLM_USAGE_FILE = process.env.LLM_USAGE_METRICS_PATH?.trim() || path.join(ROOT, "logs", "llm_usage_metrics.json");
const OUTBOX_DIR = path.join(ROOT, "outbox");

function isObject(value: unknown): value is JsonObject {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asNumber(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return parsed;
}

function ratio(numerator: number, denominator: number): number {
  if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator <= 0) {
    return 0;
  }
  return Number((numerator / denominator).toFixed(4));
}

async function readJsonFile<T>(filePath: string, fallback: T): Promise<T> {
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

async function readLlmUsageMetrics() {
  const today = new Date().toISOString().slice(0, 10);
  const payload = await readJsonFile<JsonObject>(LLM_USAGE_FILE, {});
  const daily = isObject(payload.daily) ? (payload.daily as JsonObject) : {};
  const entry = isObject(daily[today]) ? (daily[today] as JsonObject) : {};

  const total = asNumber(entry.total);
  const success = asNumber(entry.success);
  const transientError = asNumber(entry.transient_error);
  const fatalError = asNumber(entry.fatal_error);
  const quota429 = asNumber(entry.quota_429);
  const fallbackApplied = asNumber(entry.fallback_applied);

  const perAgent = isObject(entry.per_agent) ? entry.per_agent : {};
  const perModel = isObject(entry.per_model) ? entry.per_model : {};

  return {
    total,
    success,
    transientError,
    fatalError,
    quota429,
    fallbackApplied,
    successRate: ratio(success, total),
    latencyMs: {
      p95: null as number | null,
      note: "latency histogram not yet persisted",
    },
    perAgent,
    perModel,
    updatedAt: typeof payload.updated_at === "string" ? payload.updated_at : null,
  };
}

function parseOrchestrationPayload(value: unknown): JsonObject | null {
  if (!isObject(value)) {
    return null;
  }
  const orchestration = value.orchestration;
  return isObject(orchestration) ? orchestration : null;
}

async function buildOrchestrationMetrics() {
  const events = await listAgentEvents();
  const byPriority: Record<string, number> = {
    critical: 0,
    high: 0,
    normal: 0,
    low: 0,
  };
  const byTheme: Record<string, number> = {
    morning_briefing: 0,
    evening_wrapup: 0,
    adhoc: 0,
  };
  const byDecision: Record<string, number> = {
    send_now: 0,
    queue_digest: 0,
    suppressed_cooldown: 0,
    unknown: 0,
  };

  let telegramAttempted = 0;
  let telegramSent = 0;
  let autoClioAttempted = 0;
  let autoClioCreated = 0;

  for (const event of events) {
    byPriority[event.priority] = (byPriority[event.priority] ?? 0) + 1;
    byTheme[event.theme] = (byTheme[event.theme] ?? 0) + 1;

    const orchestration = parseOrchestrationPayload(event.payload);
    if (!orchestration) {
      byDecision.unknown += 1;
      continue;
    }

    const decision = String(orchestration.decision ?? "unknown");
    byDecision[decision] = (byDecision[decision] ?? 0) + 1;

    const telegram = isObject(orchestration.telegram) ? orchestration.telegram : null;
    if (telegram) {
      telegramAttempted += 1;
      if (telegram.sent === true) {
        telegramSent += 1;
      }
    }

    const autoClio = isObject(orchestration.autoClio) ? orchestration.autoClio : null;
    if (autoClio) {
      autoClioAttempted += 1;
      if (autoClio.created === true) {
        autoClioCreated += 1;
      }
    }
  }

  const approvalStats = await getApprovalQueueStats();

  return {
    totalEvents: events.length,
    byPriority,
    byTheme,
    byDecision,
    telegram: {
      attempted: telegramAttempted,
      sent: telegramSent,
      successRate: ratio(telegramSent, telegramAttempted),
    },
    autoClio: {
      attempted: autoClioAttempted,
      created: autoClioCreated,
      successRate: ratio(autoClioCreated, autoClioAttempted),
    },
    pendingApprovals: approvalStats.pending,
    approvalQueue: approvalStats,
  };
}

async function buildDeepLMetrics() {
  let files: string[] = [];
  try {
    files = await fs.readdir(OUTBOX_DIR);
  } catch {
    return {
      source: "clio_outbox",
      required: 0,
      translated: 0,
      failed: 0,
      successRate: 0,
    };
  }

  let required = 0;
  let translated = 0;
  let failed = 0;

  const targets = files.filter((item) => item.endsWith(".json")).slice(-500);
  for (const fileName of targets) {
    const filePath = path.join(OUTBOX_DIR, fileName);
    const payload = await readJsonFile<JsonObject>(filePath, {});
    const deeplRequired = payload.deepl_required === true;
    const deeplApplied = payload.deepl_applied === true;
    if (!deeplRequired) {
      continue;
    }
    required += 1;
    if (deeplApplied) {
      translated += 1;
    } else {
      failed += 1;
    }
  }

  return {
    source: "clio_outbox",
    required,
    translated,
    failed,
    successRate: ratio(translated, required),
  };
}

export async function GET() {
  const [llm, orchestration, deepl] = await Promise.all([
    readLlmUsageMetrics(),
    buildOrchestrationMetrics(),
    buildDeepLMetrics(),
  ]);

  return NextResponse.json({
    ok: true,
    generatedAt: new Date().toISOString(),
    llm,
    orchestration,
    deepl,
  });
}
