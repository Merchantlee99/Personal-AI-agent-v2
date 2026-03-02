import { NextRequest, NextResponse } from "next/server";
import { getDispatchPolicy } from "@/lib/orchestration/policy";
import { createInboxTask, findEventById, setCooldown } from "@/lib/orchestration/storage";
import { answerTelegramCallback } from "@/lib/orchestration/telegram";

type TelegramCallbackQuery = {
  id?: string;
  data?: string;
};

type TelegramUpdate = {
  update_id?: number;
  callback_query?: TelegramCallbackQuery;
};

function verifyWebhookSecret(req: NextRequest): boolean {
  const expected = (process.env.TELEGRAM_WEBHOOK_SECRET ?? "").trim();
  if (!expected) {
    return true;
  }
  const incoming = (req.headers.get("x-telegram-bot-api-secret-token") ?? "").trim();
  return incoming.length > 0 && incoming === expected;
}

function parseAction(raw?: string) {
  if (!raw) {
    return null;
  }
  const [action, eventId] = raw.split(":");
  if (!action || !eventId) {
    return null;
  }
  return { action, eventId };
}

function addHours(iso: string, hours: number) {
  const date = new Date(iso);
  date.setHours(date.getHours() + hours);
  return date.toISOString();
}

export async function POST(request: NextRequest) {
  if (!verifyWebhookSecret(request)) {
    return NextResponse.json({ error: "unauthorized_webhook" }, { status: 401 });
  }

  const update = (await request.json()) as TelegramUpdate;
  const callback = update.callback_query;
  if (!callback?.id || !callback.data) {
    return NextResponse.json({ ok: true, ignored: true, reason: "no_callback_query" });
  }

  const parsed = parseAction(callback.data);
  if (!parsed) {
    await answerTelegramCallback({ callbackQueryId: callback.id, text: "지원하지 않는 액션입니다." });
    return NextResponse.json({ ok: true, ignored: true, reason: "invalid_callback_data" });
  }

  const event = await findEventById(parsed.eventId);
  if (!event) {
    await answerTelegramCallback({ callbackQueryId: callback.id, text: "원본 이벤트를 찾을 수 없습니다." });
    return NextResponse.json({ ok: true, ignored: true, reason: "event_not_found" });
  }

  if (parsed.action === "clio_save") {
    const result = await createInboxTask({
      targetAgentId: "clio",
      reason: "telegram_inline_clio_save",
      topicKey: event.topicKey,
      title: event.title,
      summary: event.summary,
      sourceRefs: (event.sourceRefs ?? []).map((item) => ({ title: item.title, url: item.url })),
    });
    await answerTelegramCallback({
      callbackQueryId: callback.id,
      text: "Clio 저장 요청을 접수했습니다.",
    });
    return NextResponse.json({ ok: true, action: parsed.action, eventId: event.eventId, inbox: result });
  }

  if (parsed.action === "hermes_deep_dive") {
    const result = await createInboxTask({
      targetAgentId: "hermes",
      reason: "telegram_inline_hermes_deep_dive",
      topicKey: event.topicKey,
      title: event.title,
      summary: `다음 주제에 대해 심층 분석을 수행하세요: ${event.summary}`,
      sourceRefs: (event.sourceRefs ?? []).map((item) => ({ title: item.title, url: item.url })),
    });
    await answerTelegramCallback({
      callbackQueryId: callback.id,
      text: "Hermes 심층 분석 요청을 접수했습니다.",
    });
    return NextResponse.json({ ok: true, action: parsed.action, eventId: event.eventId, inbox: result });
  }

  if (parsed.action === "mute_topic") {
    const policy = getDispatchPolicy();
    const until = addHours(new Date().toISOString(), policy.cooldownHours);
    await setCooldown(event.topicKey, until);
    await answerTelegramCallback({
      callbackQueryId: callback.id,
      text: `이 주제 알림을 ${policy.cooldownHours}시간 동안 끕니다.`,
    });
    return NextResponse.json({
      ok: true,
      action: parsed.action,
      eventId: event.eventId,
      topicKey: event.topicKey,
      cooldownUntil: until,
    });
  }

  await answerTelegramCallback({ callbackQueryId: callback.id, text: "지원하지 않는 액션입니다." });
  return NextResponse.json({ ok: true, ignored: true, reason: "unsupported_action" });
}

