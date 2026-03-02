import { NextRequest, NextResponse } from "next/server";
import { normalizeAgentId } from "@/lib/agents";
import { evaluateDispatchPolicy, getDispatchPolicy, getJourneyTheme } from "@/lib/orchestration/policy";
import {
  appendAgentEvent,
  createEventId,
  getCooldown,
  makeDedupeKey,
  pushDigestItem,
} from "@/lib/orchestration/storage";
import { buildTelegramDispatchPayload, sendTelegramMessage } from "@/lib/orchestration/telegram";
import { AgentEvent, AgentEventInput, EventPriority } from "@/lib/orchestration/types";

type EventRequestBody = {
  agentId?: string;
  topicKey?: string;
  title?: string;
  summary?: string;
  priority?: EventPriority;
  confidence?: number;
  tags?: string[];
  sourceRefs?: Array<{ title: string; url: string; publisher?: string; publishedAt?: string }>;
  impactScore?: number;
  insightHint?: string;
  payload?: Record<string, unknown>;
  chatId?: string;
  forceDispatch?: boolean;
};

function isValidPriority(value: string): value is EventPriority {
  return value === "critical" || value === "high" || value === "normal" || value === "low";
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

  return {
    agentId,
    topicKey,
    title,
    summary,
    priority: priorityRaw,
    confidence,
    tags: (input.tags ?? []).map((item) => item.trim()).filter((item) => item.length > 0),
    sourceRefs: (input.sourceRefs ?? [])
      .filter((item) => item && item.title && item.url)
      .map((item) => ({
        title: item.title.trim(),
        url: item.url.trim(),
        publisher: item.publisher?.trim(),
        publishedAt: item.publishedAt?.trim(),
      })),
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

export async function POST(request: NextRequest) {
  const payload = (await request.json()) as EventRequestBody;
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

  await appendAgentEvent(event);

  if (outcome.decision === "queue_digest" || outcome.decision === "suppressed_cooldown") {
    const digestSlot = pickDigestSlot(policy.digestSlots, event.theme);
    await pushDigestItem(digestSlot, event);
  }

  let telegram: { sent: boolean; reason: string } = { sent: false, reason: "not_attempted" };
  const chatId = (payload.chatId ?? process.env.TELEGRAM_CHAT_ID ?? "").trim();
  if (outcome.decision === "send_now" && chatId) {
    const dispatchPayload = buildTelegramDispatchPayload({ chatId, event });
    const sendResult = await sendTelegramMessage(dispatchPayload);
    telegram = sendResult.sent ? { sent: true, reason: "ok" } : { sent: false, reason: sendResult.reason };
  }

  return NextResponse.json({
    ok: true,
    eventId: event.eventId,
    theme: event.theme,
    decision: outcome.decision,
    reason: outcome.reason,
    mode: outcome.mode,
    policy,
    cooldownUntil: outcome.cooldownUntil ?? null,
    telegram,
  });
}
