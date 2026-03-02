import { AgentEvent, TelegramDispatchPayload, TelegramInlineKeyboard } from "@/lib/orchestration/types";

const PRIORITY_EMOJI: Record<AgentEvent["priority"], string> = {
  critical: "🚨",
  high: "🔔",
  normal: "🧭",
  low: "📝",
};

function cleanLine(value: string): string {
  return value
    .replace(/#+\s*/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function shortText(value: string, limit: number): string {
  const line = cleanLine(value);
  if (line.length <= limit) {
    return line;
  }
  return `${line.slice(0, limit - 1).trimEnd()}…`;
}

function buildSources(event: AgentEvent): string[] {
  if (!event.sourceRefs || event.sourceRefs.length === 0) {
    return ["출처: 내부 요약 이벤트"];
  }
  return event.sourceRefs.slice(0, 3).map((source, index) => {
    const title = shortText(source.title, 52);
    return `출처${index + 1}: ${title} (${source.url})`;
  });
}

export function createInlineKeyboard(eventId: string): TelegramInlineKeyboard {
  return {
    inline_keyboard: [
      [{ text: "Clio에 저장", callback_data: `clio_save:${eventId}` }],
      [{ text: "Hermes, 더 파고들어 줘", callback_data: `hermes_deep_dive:${eventId}` }],
      [{ text: "이 주제 알림 끄기", callback_data: `mute_topic:${eventId}` }],
    ],
  };
}

export function renderMinervaTelegramText(event: AgentEvent): string {
  const emoji = PRIORITY_EMOJI[event.priority];
  const title = shortText(event.title, 80);
  const summary = shortText(event.summary, 180);
  const insight = shortText(
    event.insightHint || "연결 가능한 신호를 확인했고, 다음 액션 우선순위를 조정하는 것이 좋겠습니다.",
    160
  );
  const sources = buildSources(event);

  return [
    `${emoji} Minerva 브리핑`,
    `주제: ${title}`,
    `요약: ${summary}`,
    ...sources,
    `인사이트: ${insight}`,
    `선택: 아래 버튼으로 저장/심층분석/알림억제를 실행할 수 있습니다.`,
  ].join("\n");
}

export function buildTelegramDispatchPayload(params: {
  chatId: string;
  event: AgentEvent;
}): TelegramDispatchPayload {
  return {
    chat_id: params.chatId,
    text: renderMinervaTelegramText(params.event),
    disable_web_page_preview: true,
    reply_markup: createInlineKeyboard(params.event.eventId),
  };
}

export async function sendTelegramMessage(payload: TelegramDispatchPayload) {
  const token = process.env.TELEGRAM_BOT_TOKEN?.trim();
  if (!token) {
    return { sent: false, reason: "telegram_token_missing" as const };
  }
  const endpoint = `https://api.telegram.org/bot${token}/sendMessage`;
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });

  if (!response.ok) {
    const body = await response.text();
    return { sent: false, reason: "telegram_send_failed" as const, status: response.status, detail: body };
  }
  const body = await response.json();
  return { sent: true, reason: "ok" as const, response: body };
}

export async function answerTelegramCallback(params: {
  callbackQueryId: string;
  text: string;
  showAlert?: boolean;
}) {
  const token = process.env.TELEGRAM_BOT_TOKEN?.trim();
  if (!token) {
    return { ok: false, reason: "telegram_token_missing" as const };
  }
  const endpoint = `https://api.telegram.org/bot${token}/answerCallbackQuery`;
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      callback_query_id: params.callbackQueryId,
      text: shortText(params.text, 120),
      show_alert: Boolean(params.showAlert),
    }),
    cache: "no-store",
  });
  if (!response.ok) {
    return { ok: false, reason: "telegram_answer_failed" as const, status: response.status };
  }
  return { ok: true };
}

