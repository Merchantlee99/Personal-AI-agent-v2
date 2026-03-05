import { NextRequest, NextResponse } from "next/server";
import { normalizeAgentId } from "@/lib/agents";
import { evaluateDispatchPolicy, getDispatchPolicy, getJourneyTheme } from "@/lib/orchestration/policy";
import {
  appendAgentEvent,
  createInboxTask,
  createEventId,
  getCooldown,
  makeDedupeKey,
  pushDigestItem,
} from "@/lib/orchestration/storage";
import { buildTelegramDispatchPayload, sendTelegramMessage } from "@/lib/orchestration/telegram";
import { listGoogleTodayEvents, isGoogleCalendarEnabled } from "@/lib/integrations/google-calendar";
import { AgentEvent, AgentEventInput, EventPriority, MinervaCalendarBriefing } from "@/lib/orchestration/types";
import { annotateSourceRefs } from "@/lib/orchestration/source-taxonomy";
import {
  ORCHESTRATION_EVENT_SCHEMA_VERSION,
  validateEventContractV1,
} from "@/lib/orchestration/event-contract";

type EventRequestBody = {
  schemaVersion?: number;
  agentId?: string;
  topicKey?: string;
  title?: string;
  summary?: string;
  priority?: EventPriority;
  confidence?: number;
  tags?: string[];
  sourceRefs?: Array<{
    title: string;
    url: string;
    snippet?: string;
    publisher?: string;
    publishedAt?: string;
    category?: string;
    priorityTier?: "P0" | "P1" | "P2";
    domain?: string;
  }>;
  impactScore?: number;
  insightHint?: string;
  payload?: Record<string, unknown>;
  chatId?: string;
  forceDispatch?: boolean;
};

function isValidPriority(value: string): value is EventPriority {
  return value === "critical" || value === "high" || value === "normal" || value === "low";
}

function normalizePriorityTier(value: string | undefined): "P0" | "P1" | "P2" | undefined {
  const raw = (value ?? "").trim().toUpperCase();
  if (raw === "P0" || raw === "P1" || raw === "P2") {
    return raw;
  }
  return undefined;
}

function normalizeInput(input: EventRequestBody): AgentEventInput | null {
  const agentId = normalizeAgentId(input.agentId ?? "");
  if (!agentId) {
    return null;
  }
  const topicKey = (input.topicKey ?? "").trim().toLowerCase();
  const title = (input.title ?? "").trim();
  const summary = (input.summary ?? "").trim();
  const priorityRaw = (input.priority ?? "").trim().toLowerCase();
  const confidence = Number(input.confidence ?? 0);
  if (!topicKey || !title || !summary || !isValidPriority(priorityRaw)) {
    return null;
  }

  const sourceRefs = annotateSourceRefs(
    (input.sourceRefs ?? [])
      .filter((item) => item && item.title && item.url)
      .map((item) => ({
        title: item.title.trim(),
        url: item.url.trim(),
        snippet: item.snippet?.trim(),
        publisher: item.publisher?.trim(),
        publishedAt: item.publishedAt?.trim(),
        category: item.category?.trim(),
        priorityTier: normalizePriorityTier(item.priorityTier),
        domain: item.domain?.trim(),
      }))
  );

  const tags = (input.tags ?? []).map((item) => item.trim()).filter((item) => item.length > 0);
  for (const source of sourceRefs) {
    if (source.category) {
      tags.push(`source:${source.category}`);
    }
    if (source.priorityTier) {
      tags.push(`tier:${source.priorityTier.toLowerCase()}`);
    }
  }

  return {
    agentId,
    topicKey,
    title,
    summary,
    priority: priorityRaw,
    confidence,
    tags: Array.from(new Set(tags)),
    sourceRefs,
    impactScore: Number.isFinite(input.impactScore) ? input.impactScore : undefined,
    insightHint: input.insightHint?.trim(),
    payload: input.payload ?? {},
  };
}

function pickDigestSlot(slots: string[], theme: AgentEvent["theme"]): string {
  if (slots.length === 0) {
    return "18:00";
  }
  if (theme === "morning_briefing") {
    return slots[0];
  }
  if (theme === "evening_wrapup") {
    return slots[1] ?? slots[0];
  }
  return slots[slots.length - 1];
}

function parseBoolean(raw: string | undefined, fallback: boolean): boolean {
  if (!raw) {
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

function parseNumber(raw: string | undefined, fallback: number): number {
  if (!raw) {
    return fallback;
  }
  const parsed = Number(raw.trim());
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return parsed;
}

function formatCalendarTimeLabel(value: string | null): string {
  if (!value) {
    return "시간미정";
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return "종일";
  }
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) {
    return "시간미정";
  }
  const timezone = (process.env.MINERVA_BRIEFING_TIMEZONE ?? "Asia/Seoul").trim() || "Asia/Seoul";
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: timezone,
  }).format(date);
}

async function loadMorningCalendarBriefing(theme: AgentEvent["theme"]): Promise<MinervaCalendarBriefing | null> {
  if (theme !== "morning_briefing") {
    return null;
  }
  if (!parseBoolean(process.env.MINERVA_MORNING_INCLUDE_CALENDAR, true)) {
    return null;
  }
  if (!isGoogleCalendarEnabled()) {
    return null;
  }

  try {
    const result = await listGoogleTodayEvents({});
    if (result.events.length === 0) {
      return {
        summary: "오늘 등록된 일정이 없습니다.",
        items: [],
      };
    }
    return {
      summary: `오늘 일정 ${result.events.length}건이 있습니다.`,
      items: result.events.slice(0, 3).map((event) => ({
        timeLabel: formatCalendarTimeLabel(event.start),
        title: event.summary,
      })),
    };
  } catch (error) {
    const detail = error instanceof Error ? error.message : "unknown_error";
    if (detail.includes("not_connected")) {
      return {
        summary: "캘린더 연결 확인이 필요합니다.",
        items: [],
      };
    }
    return {
      summary: "캘린더 조회가 일시적으로 실패했습니다.",
      items: [],
    };
  }
}

function shouldAutoSaveClio(event: AgentEvent): { shouldRun: boolean; reason: string } {
  const enabled = parseBoolean(process.env.HERMES_AUTO_CLIO_SAVE, true);
  if (!enabled) {
    return { shouldRun: false, reason: "disabled" };
  }
  if (event.agentId !== "hermes") {
    return { shouldRun: false, reason: "agent_not_hermes" };
  }
  if (event.priority === "critical") {
    return { shouldRun: true, reason: "critical_priority" };
  }
  if (event.priority !== "high") {
    return { shouldRun: false, reason: "priority_below_high" };
  }

  const minImpact = parseNumber(process.env.HERMES_AUTO_CLIO_SAVE_MIN_IMPACT, 0.75);
  const impactScore = Number(event.impactScore ?? 0);
  const tags = new Set((event.tags ?? []).map((token) => token.toLowerCase()));
  const hasKnowledgeTag = ["research", "paper", "analysis", "insight", "whitepaper"].some((tag) => tags.has(tag));

  if (impactScore >= minImpact || hasKnowledgeTag) {
    return { shouldRun: true, reason: "high_impact_or_knowledge_tag" };
  }
  return { shouldRun: false, reason: "impact_below_threshold" };
}

export async function POST(request: NextRequest) {
  const rawBody = (await request.json()) as unknown;
  const contract = validateEventContractV1(rawBody, {
    requireExplicitSchemaVersion: parseBoolean(process.env.ORCH_REQUIRE_SCHEMA_V1, false),
  });
  if (!contract.ok) {
    return NextResponse.json(
      {
        error: contract.error,
        schemaVersion: contract.schemaVersion,
        mode: contract.mode,
        required: contract.required,
        issues: contract.issues,
      },
      { status: 400 }
    );
  }

  const payload = contract.payload as EventRequestBody;
  const normalized = normalizeInput(payload);
  if (!normalized) {
    return NextResponse.json(
      {
        error: "invalid_event_payload_after_contract_validation",
        schemaVersion: ORCHESTRATION_EVENT_SCHEMA_VERSION,
        required: ["agentId", "topicKey", "title", "summary", "priority", "confidence"],
      },
      { status: 400 }
    );
  }

  const now = new Date();
  const theme = getJourneyTheme(now);
  const event: AgentEvent = {
    ...normalized,
    eventId: createEventId(),
    createdAt: now.toISOString(),
    theme,
    dedupeKey: makeDedupeKey(normalized.topicKey, normalized.summary),
  };

  const policy = getDispatchPolicy();
  const cooldownUntil = await getCooldown(normalized.topicKey);
  const outcome = payload.forceDispatch
    ? { decision: "send_now" as const, reason: "force_dispatch", mode: "immediate" as const }
    : evaluateDispatchPolicy({
        priority: normalized.priority,
        confidence: normalized.confidence,
        policy,
        cooldownUntil,
        now,
      });

  const autoClioPolicy = shouldAutoSaveClio(event);
  let autoClio: { created: boolean; reason: string; inboxFile?: string; path?: string; error?: string } = {
    created: false,
    reason: autoClioPolicy.reason,
  };
  if (autoClioPolicy.shouldRun) {
    try {
      const task = await createInboxTask({
        targetAgentId: "clio",
        reason: "hermes_high_impact_auto_clio_save",
        topicKey: event.topicKey,
        title: event.title,
        summary: event.summary,
        sourceRefs: (event.sourceRefs ?? []).map((item) => ({ title: item.title, url: item.url })),
      });
      autoClio = {
        created: true,
        reason: autoClioPolicy.reason,
        inboxFile: task.inboxFile,
        path: task.path,
      };
    } catch (error) {
      autoClio = {
        created: false,
        reason: "create_inbox_failed",
        error: error instanceof Error ? error.message : "unknown_error",
      };
    }
  }

  if (outcome.decision === "queue_digest" || outcome.decision === "suppressed_cooldown") {
    const digestSlot = pickDigestSlot(policy.digestSlots, event.theme);
    await pushDigestItem(digestSlot, event);
  }

  let telegram: { sent: boolean; reason: string } = { sent: false, reason: "not_attempted" };
  let calendarBriefing: MinervaCalendarBriefing | null = null;
  const chatId = (payload.chatId ?? process.env.TELEGRAM_CHAT_ID ?? "").trim();
  if (outcome.decision === "send_now" && chatId) {
    calendarBriefing = await loadMorningCalendarBriefing(event.theme);
    const dispatchPayload = await buildTelegramDispatchPayload({ chatId, event, calendarBriefing });
    const sendResult = await sendTelegramMessage(dispatchPayload);
    telegram = sendResult.sent ? { sent: true, reason: "ok" } : { sent: false, reason: sendResult.reason };
  }

  await appendAgentEvent({
    ...event,
    payload: {
      ...(event.payload ?? {}),
      orchestration: {
        schemaVersion: ORCHESTRATION_EVENT_SCHEMA_VERSION,
        contractMode: contract.mode,
        decision: outcome.decision,
        reason: outcome.reason,
        mode: outcome.mode,
        cooldownUntil: outcome.cooldownUntil ?? null,
        telegram,
        autoClio: {
          created: autoClio.created,
          reason: autoClio.reason,
        },
      },
    },
  });

  return NextResponse.json({
    ok: true,
    schemaVersion: ORCHESTRATION_EVENT_SCHEMA_VERSION,
    contractMode: contract.mode,
    eventId: event.eventId,
    theme: event.theme,
    decision: outcome.decision,
    reason: outcome.reason,
    mode: outcome.mode,
    policy,
    cooldownUntil: outcome.cooldownUntil ?? null,
    telegram,
    calendarBriefingAttached: calendarBriefing !== null,
    autoClio,
  });
}
