import fs from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";
import { listTelegramPendingApprovals } from "@/lib/orchestration/storage";

type DailyUsage = {
  total?: number;
  success?: number;
  transient_error?: number;
  fatal_error?: number;
  quota_429?: number;
  fallback_applied?: number;
  latency_ms_total?: number;
  latency_ms_count?: number;
  latency_ms_max?: number;
  latency_ms_samples?: number[];
  per_agent?: Record<string, number>;
  per_model?: Record<string, number>;
};

type DailyDeepLUsage = {
  attempts?: number;
  translated?: number;
  cached?: number;
  skipped?: number;
  failed?: number;
  input_chars?: number;
  translated_chars?: number;
};

type EventDecision = "send_now" | "queue_digest" | "suppressed_cooldown";

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function asBoolean(value: unknown): boolean {
  return value === true;
}

function asDecision(value: unknown): EventDecision | null {
  if (value === "send_now" || value === "queue_digest" || value === "suppressed_cooldown") {
    return value;
  }
  return null;
}

function computeP95(samples: number[]): number {
  if (samples.length === 0) {
    return 0;
  }
  const sorted = [...samples].filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (sorted.length === 0) {
    return 0;
  }
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * 0.95) - 1));
  return Math.round(sorted[index] * 10) / 10;
}

function todayKey() {
  return new Date().toISOString().slice(0, 10);
}

async function readJson<T>(filePath: string, fallback: T): Promise<T> {
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export async function GET() {
  const root = process.env.SHARED_ROOT_PATH?.trim() || path.join(process.cwd(), "shared_data");
  const usagePath = path.join(root, "logs", "llm_usage_metrics.json");
  const deepLUsagePath = path.join(root, "logs", "deepl_usage_metrics.json");
  const eventsPath = path.join(root, "shared_memory", "agent_events.json");

  const usageRaw = await readJson<{ daily?: Record<string, DailyUsage>; updated_at?: string }>(usagePath, {});
  const deepLUsageRaw = await readJson<{ daily?: Record<string, DailyDeepLUsage>; updated_at?: string }>(deepLUsagePath, {});
  const dayUsage = usageRaw.daily?.[todayKey()] ?? {};
  const dayDeepL = deepLUsageRaw.daily?.[todayKey()] ?? {};
  const total = Number(dayUsage.total ?? 0);
  const success = Number(dayUsage.success ?? 0);
  const transientError = Number(dayUsage.transient_error ?? 0);
  const fatalError = Number(dayUsage.fatal_error ?? 0);
  const quota429 = Number(dayUsage.quota_429 ?? 0);
  const fallbackApplied = Number(dayUsage.fallback_applied ?? 0);
  const successRate = total > 0 ? Math.round((success / total) * 1000) / 10 : 100;
  const latencyCount = Number(dayUsage.latency_ms_count ?? 0);
  const latencyTotal = Number(dayUsage.latency_ms_total ?? 0);
  const latencyAvg = latencyCount > 0 ? Math.round((latencyTotal / latencyCount) * 10) / 10 : 0;
  const latencyP95 = computeP95(Array.isArray(dayUsage.latency_ms_samples) ? dayUsage.latency_ms_samples : []);
  const latencyMax = Math.round(Number(dayUsage.latency_ms_max ?? 0) * 10) / 10;

  const events = await readJson<Array<{ createdAt?: string; agentId?: string; payload?: unknown }>>(eventsPath, []);
  const today = todayKey();
  const todayEvents = events.filter((item) => typeof item.createdAt === "string" && item.createdAt.startsWith(today));
  const decisionCounts: Record<EventDecision, number> = {
    send_now: 0,
    queue_digest: 0,
    suppressed_cooldown: 0,
  };
  const agentCounts: Record<string, number> = {};
  let telegramAttempted = 0;
  let telegramSent = 0;
  let autoClioCreated = 0;

  for (const event of todayEvents) {
    if (typeof event.agentId === "string" && event.agentId.trim().length > 0) {
      const key = event.agentId.trim().toLowerCase();
      agentCounts[key] = Number(agentCounts[key] ?? 0) + 1;
    }

    const payload = asRecord(event.payload);
    const dispatch = asRecord(payload?._dispatch);
    const decision = asDecision(dispatch?.decision);
    if (decision) {
      decisionCounts[decision] += 1;
    }

    const telegram = asRecord(dispatch?.telegram);
    const attempted = asBoolean(telegram?.attempted) || typeof dispatch?.telegramReason === "string";
    const sent = asBoolean(telegram?.sent) || dispatch?.telegramReason === "ok";
    if (attempted) {
      telegramAttempted += 1;
    }
    if (sent) {
      telegramSent += 1;
    }
    if (asBoolean(dispatch?.autoClioCreated)) {
      autoClioCreated += 1;
    }
  }
  const telegramFailed = Math.max(0, telegramAttempted - telegramSent);
  const telegramSuccessRate = telegramAttempted > 0 ? Math.round((telegramSent / telegramAttempted) * 1000) / 10 : 100;

  const pendingApprovals = await listTelegramPendingApprovals({
    statuses: ["pending_step1", "pending_step2"],
  });
  const allApprovals = await listTelegramPendingApprovals();
  const approvalCounts = {
    pending_step1: 0,
    pending_step2: 0,
    approved: 0,
    rejected: 0,
    expired: 0,
  };
  for (const approval of allApprovals) {
    if (approval.status in approvalCounts) {
      approvalCounts[approval.status as keyof typeof approvalCounts] += 1;
    }
  }

  const deepLAttempts = Number(dayDeepL.attempts ?? 0);
  const deepLTranslated = Number(dayDeepL.translated ?? 0);
  const deepLCached = Number(dayDeepL.cached ?? 0);
  const deepLSkipped = Number(dayDeepL.skipped ?? 0);
  const deepLFailed = Number(dayDeepL.failed ?? 0);
  const deepLInputChars = Number(dayDeepL.input_chars ?? 0);
  const deepLTranslatedChars = Number(dayDeepL.translated_chars ?? 0);
  const deepLSuccessRate = deepLAttempts > 0 ? Math.round((deepLTranslated / deepLAttempts) * 1000) / 10 : 100;

  return NextResponse.json({
    ok: true,
    day: today,
    updatedAt: deepLUsageRaw.updated_at ?? usageRaw.updated_at ?? null,
    llm: {
      total,
      success,
      transientError,
      fatalError,
      quota429,
      fallbackApplied,
      successRate,
      latencyMs: {
        avg: latencyAvg,
        p95: latencyP95,
        max: latencyMax,
        samples: latencyCount,
      },
      perAgent: dayUsage.per_agent ?? {},
      perModel: dayUsage.per_model ?? {},
    },
    orchestration: {
      todayEvents: todayEvents.length,
      byDecision: decisionCounts,
      byAgent: agentCounts,
      autoClioCreated,
      telegram: {
        attempted: telegramAttempted,
        sent: telegramSent,
        failed: telegramFailed,
        successRate: telegramSuccessRate,
      },
      pendingApprovals: pendingApprovals.length,
      approvals: approvalCounts,
    },
    deepl: {
      attempts: deepLAttempts,
      translated: deepLTranslated,
      cached: deepLCached,
      skipped: deepLSkipped,
      failed: deepLFailed,
      inputChars: deepLInputChars,
      translatedChars: deepLTranslatedChars,
      successRate: deepLSuccessRate,
    },
  });
}
