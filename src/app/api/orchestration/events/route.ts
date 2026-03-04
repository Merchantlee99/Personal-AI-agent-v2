import { NextRequest, NextResponse } from "next/server";
import { normalizeAgentId } from "@/lib/agents";
import {
  evaluateAutoClioPolicy,
  evaluateDispatchPolicy,
  getAutoClioPolicy,
  getDispatchPolicy,
  getJourneyTheme,
} from "@/lib/orchestration/policy";
import {
  appendAgentEvent,
  createInboxTask,
  createEventId,
  getCooldown,
  makeDedupeKey,
  pushDigestItem,
} from "@/lib/orchestration/storage";
import { dispatchBriefingToPrimaryChannel } from "@/lib/orchestration/channels";
import { listGoogleTodayEvents, isGoogleCalendarEnabled } from "@/lib/integrations/google-calendar";
import { AgentEvent, AgentEventInput, EventPriority, MinervaCalendarBriefing } from "@/lib/orchestration/types";
import { annotateSourceRefs } from "@/lib/orchestration/source-taxonomy";
import { parseOrchestrationEventContract } from "@/lib/orchestration/event-contract";
import { scoreEventSignal } from "@/lib/orchestration/source-scoring";

type EventRequestBody = {
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

export async function POST(request: NextRequest) {
  const raw = await request.json();
  const parsedContract = parseOrchestrationEventContract(raw);
  if (!parsedContract.ok) {
    return NextResponse.json(
      {
        error: parsedContract.error,
        detail: parsedContract.detail,
        validationErrors: parsedContract.errors ?? [],
        required: ["schemaVersion", "eventType", "producer", "occurredAt", "payload"],
      },
      { status: 400 }
    );
  }

  const payload = parsedContract.payload as EventRequestBody;
  const normalized = normalizeInput(payload);
  if (!normalized) {
    return NextResponse.json(
      {
        error: "invalid_event_payload",
        required: ["agentId", "topicKey", "title", "summary", "priority", "confidence"],
      },
      { status: 400 }
    );
  }

  const now = new Date();
  const theme = getJourneyTheme(now);
  const signalScore = scoreEventSignal(normalized);
  const effectiveConfidence = Math.max(normalized.confidence, signalScore.computedConfidence);
  const event: AgentEvent = {
    ...normalized,
    confidence: effectiveConfidence,
    payload: {
      ...(normalized.payload ?? {}),
      signal_score: signalScore,
      _contract: parsedContract.contract,
    },
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
        confidence: effectiveConfidence,
        alertScore: signalScore.alertScore,
        policy,
        cooldownUntil,
        now,
      });

  const autoClioPolicy = evaluateAutoClioPolicy({
    agentId: event.agentId,
    priority: event.priority,
    impactScore: event.impactScore,
    tags: event.tags,
    policy: getAutoClioPolicy(),
  });
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
    const sendResult = await dispatchBriefingToPrimaryChannel({ chatId, event, calendarBriefing });
    telegram = sendResult.sent ? { sent: true, reason: "ok" } : { sent: false, reason: sendResult.reason };
  }

  const eventForStore: AgentEvent = {
    ...event,
    payload: {
      ...(event.payload ?? {}),
      _dispatch: {
        decision: outcome.decision,
        reason: outcome.reason,
        mode: outcome.mode,
        telegram: {
          attempted: outcome.decision === "send_now" && Boolean(chatId),
          sent: telegram.sent,
          reason: telegram.reason,
          chatConfigured: Boolean(chatId),
        },
        autoClioCreated: autoClio.created,
        autoClioReason: autoClio.reason,
        calendarBriefingAttached: calendarBriefing !== null,
      },
    },
  };
  await appendAgentEvent(eventForStore);

  return NextResponse.json({
    ok: true,
    contract: parsedContract.contract,
    eventId: event.eventId,
    theme: event.theme,
    decision: outcome.decision,
    reason: outcome.reason,
    mode: outcome.mode,
    signalScore,
    policy,
    cooldownUntil: outcome.cooldownUntil ?? null,
    telegram,
    calendarBriefingAttached: calendarBriefing !== null,
    autoClio,
  });
}
