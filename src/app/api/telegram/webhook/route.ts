import { NextRequest, NextResponse } from "next/server";
import {
  appendTelegramChatHistory,
  clearTelegramChatHistory,
  createInboxTask,
  findEventById,
  getTelegramChatHistory,
} from "@/lib/orchestration/storage";
import { answerTelegramCallback, sendTelegramTextMessage } from "@/lib/orchestration/telegram";

type TelegramActor = {
  id?: number | string;
  username?: string;
};

type TelegramChat = {
  id?: number | string;
  type?: string;
};

type TelegramCallbackQuery = {
  id?: string;
  data?: string;
  from?: TelegramActor;
  message?: {
    chat?: TelegramChat;
  };
};

type TelegramMessage = {
  message_id?: number;
  text?: string;
  from?: TelegramActor;
  chat?: TelegramChat;
};

type TelegramUpdate = {
  update_id?: number;
  callback_query?: TelegramCallbackQuery;
  message?: TelegramMessage;
};

type AllowlistResult =
  | {
      ok: true;
      userId: string;
      chatId: string;
    }
  | {
      ok: false;
      reason: string;
    };

const textRateWindow = new Map<string, { windowStart: number; count: number }>();

function verifyWebhookSecret(req: NextRequest): boolean {
  const expected = (process.env.TELEGRAM_WEBHOOK_SECRET ?? "").trim();
  if (!expected) {
    return true;
  }
  const incoming = (req.headers.get("x-telegram-bot-api-secret-token") ?? "").trim();
  return incoming.length > 0 && incoming === expected;
}

function parseAllowlist(raw: string | undefined): Set<string> {
  if (!raw) {
    return new Set();
  }
  return new Set(
    raw
      .split(",")
      .map((token) => token.trim())
      .filter((token) => token.length > 0)
  );
}

function isAllowedAction(action: string): boolean {
  const configured = parseAllowlist(process.env.TELEGRAM_ALLOWED_CALLBACK_ACTIONS);
  if (configured.size === 0) {
    return action === "clio_save" || action === "hermes_deep_dive" || action === "minerva_insight";
  }
  return configured.has(action);
}

function verifyAllowlist(source: { from?: TelegramActor; chat?: TelegramChat }): AllowlistResult {
  const allowedUsers = parseAllowlist(process.env.TELEGRAM_ALLOWED_USER_IDS);
  const allowedChats = parseAllowlist(process.env.TELEGRAM_ALLOWED_CHAT_IDS);

  const userId = source.from?.id !== undefined ? String(source.from.id) : "";
  const chatId = source.chat?.id !== undefined ? String(source.chat.id) : "";

  if (allowedUsers.size > 0) {
    if (!userId) {
      return { ok: false, reason: "missing_user_id" };
    }
    if (!allowedUsers.has(userId)) {
      return { ok: false, reason: "user_not_allowed" };
    }
  }

  if (allowedChats.size > 0) {
    if (!chatId) {
      return { ok: false, reason: "missing_chat_id" };
    }
    if (!allowedChats.has(chatId)) {
      return { ok: false, reason: "chat_not_allowed" };
    }
  }

  return { ok: true, userId, chatId };
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

function parseIntEnv(name: string, fallback: number, minValue: number): number {
  const raw = (process.env[name] ?? "").trim();
  if (!raw) {
    return fallback;
  }
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(minValue, Math.trunc(parsed));
}

function checkTextRateLimit(chatId: string): { ok: true } | { ok: false; retryAfterSec: number } {
  const windowSec = parseIntEnv("TELEGRAM_TEXT_RATE_LIMIT_WINDOW_SEC", 60, 1);
  const maxPerWindow = parseIntEnv("TELEGRAM_TEXT_RATE_LIMIT_MAX", 12, 1);
  const nowMs = Date.now();
  const windowStart = nowMs - (nowMs % (windowSec * 1000));
  const entry = textRateWindow.get(chatId);

  if (!entry || entry.windowStart !== windowStart) {
    textRateWindow.set(chatId, { windowStart, count: 1 });
    return { ok: true };
  }

  if (entry.count >= maxPerWindow) {
    const retryAfterSec = Math.max(1, Math.ceil((windowStart + windowSec * 1000 - nowMs) / 1000));
    return { ok: false, retryAfterSec };
  }

  textRateWindow.set(chatId, { windowStart, count: entry.count + 1 });
  return { ok: true };
}

function resolveAppBaseUrl(request: NextRequest): string {
  const configured = (process.env.INTERNAL_APP_BASE_URL ?? process.env.NEXT_PUBLIC_APP_URL ?? "").trim();
  if (configured) {
    return configured.replace(/\/+$/, "");
  }
  const host = (request.headers.get("x-forwarded-host") ?? request.headers.get("host") ?? "127.0.0.1:3000").trim();
  const proto = (request.headers.get("x-forwarded-proto") ?? "http").trim();
  return `${proto}://${host}`;
}

function compactLine(value: string, maxLen: number): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLen) {
    return normalized;
  }
  return `${normalized.slice(0, maxLen - 1).trimEnd()}…`;
}

function formatTelegramPlainText(value: string, maxLen: number): string {
  const lines = value
    .replace(/\r/g, "")
    .replace(/\*\*/g, "")
    .split("\n")
    .map((line) => line.replace(/^\s{0,3}#{1,6}\s*/g, "").trim())
    .map((line) => line.replace(/^["“”'`]+|["“”'`]+$/g, ""))
    .map((line) => line.replace(/\s+/g, " ").trim());

  const normalized = lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  if (normalized.length <= maxLen) {
    return normalized;
  }
  return `${normalized.slice(0, maxLen - 1).trimEnd()}…`;
}

async function callMinervaChat(params: {
  request: NextRequest;
  message: string;
  history: Array<{ role: string; text: string; at?: string | null }>;
}): Promise<{ reply: string; model?: string }> {
  const timeoutMs = parseIntEnv("TELEGRAM_MINERVA_TIMEOUT_MS", 12000, 1000);
  const maxRetries = parseIntEnv("TELEGRAM_MINERVA_RETRY_MAX", 2, 1);
  const retryableStatuses = new Set([429, 500, 502, 503, 504]);
  const endpoint = `${resolveAppBaseUrl(params.request)}/api/chat`;
  let lastError: string | null = null;

  for (let attempt = 1; attempt <= maxRetries; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          agentId: "minerva",
          message: params.message,
          history: params.history,
        }),
        cache: "no-store",
        signal: controller.signal,
      });

      if (response.ok) {
        const payload = (await response.json()) as { reply?: string; model?: string };
        const reply = formatTelegramPlainText(String(payload.reply ?? "").trim(), 3200);
        if (!reply) {
          throw new Error("empty_reply");
        }
        return { reply, model: payload.model };
      }

      const detail = await response.text();
      lastError = `status_${response.status}:${detail.slice(0, 200)}`;
      if (!retryableStatuses.has(response.status) || attempt === maxRetries) {
        throw new Error(lastError);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "chat_request_failed";
      lastError = message;
      if (attempt === maxRetries) {
        throw new Error(message);
      }
    } finally {
      clearTimeout(timer);
    }
  }

  throw new Error(lastError ?? "chat_request_failed");
}

async function handleCallbackUpdate(callback: TelegramCallbackQuery) {
  if (!callback.id || !callback.data) {
    return NextResponse.json({ ok: true, ignored: true, reason: "no_callback_query" });
  }

  const parsed = parseAction(callback.data);
  if (!parsed) {
    await answerTelegramCallback({ callbackQueryId: callback.id, text: "지원하지 않는 액션입니다." });
    return NextResponse.json({ ok: true, ignored: true, reason: "invalid_callback_data" });
  }
  if (!isAllowedAction(parsed.action)) {
    await answerTelegramCallback({ callbackQueryId: callback.id, text: "허용되지 않은 액션입니다.", showAlert: true });
    return NextResponse.json({ ok: true, ignored: true, reason: "action_not_allowed" });
  }

  const allowlist = verifyAllowlist({ from: callback.from, chat: callback.message?.chat });
  if (!allowlist.ok) {
    await answerTelegramCallback({
      callbackQueryId: callback.id,
      text: "권한이 없는 요청입니다.",
      showAlert: true,
    });
    return NextResponse.json({ error: "forbidden_callback_source", reason: allowlist.reason }, { status: 403 });
  }

  const event = await findEventById(parsed.eventId);
  if (!event) {
    await answerTelegramCallback({ callbackQueryId: callback.id, text: "원본 이벤트를 찾을 수 없습니다." });
    return NextResponse.json({ ok: true, ignored: true, reason: "event_not_found" });
  }

  if (parsed.action === "clio_save") {
    const result = await createInboxTask({
      targetAgentId: "clio",
      reason: "telegram_inline_clio_obsidian_save",
      topicKey: event.topicKey,
      title: event.title,
      summary:
        `다음 내용을 Clio Obsidian 저장 포맷으로 정리해 저장하세요.\n` +
        `- 핵심 요약: ${event.summary}\n` +
        `- 필수 출력: 태그, 관련 노트 링크, 출처 URL, notebooklm_ready 메타`,
      sourceRefs: (event.sourceRefs ?? []).map((item) => ({ title: item.title, url: item.url })),
    });
    await answerTelegramCallback({
      callbackQueryId: callback.id,
      text: "Clio 옵시디언 저장 요청을 접수했습니다.",
    });
    return NextResponse.json({ ok: true, mode: "callback_query", action: parsed.action, eventId: event.eventId, inbox: result });
  }

  if (parsed.action === "hermes_deep_dive") {
    const result = await createInboxTask({
      targetAgentId: "hermes",
      reason: "telegram_inline_hermes_find_more",
      topicKey: event.topicKey,
      title: event.title,
      summary:
        `다음 주제와 직접 관련된 뉴스/아티클/트렌드 신호를 더 찾아주세요.\n` +
        `- 기준 요약: ${event.summary}\n` +
        `- 역할 제한: 사실/근거 수집만 수행하고, 최종 판단·전략 결론은 작성하지 마세요.\n` +
        `- 요청 출력: 관련 출처 5개 이상, 상충 관점 1개 이상, 핵심 변화 요약(데이터 중심)\n` +
        `- 후속 처리: 처리 완료 후 Minerva 인사이트 분석 태스크가 자동 생성됩니다.`,
      sourceRefs: (event.sourceRefs ?? []).map((item) => ({ title: item.title, url: item.url })),
    });
    await answerTelegramCallback({
      callbackQueryId: callback.id,
      text: "Hermes 근거 수집 요청을 접수했습니다. 완료 후 Minerva 분석이 자동 연결됩니다.",
    });
    return NextResponse.json({ ok: true, mode: "callback_query", action: parsed.action, eventId: event.eventId, inbox: result });
  }

  if (parsed.action === "minerva_insight") {
    const result = await createInboxTask({
      targetAgentId: "minerva",
      reason: "telegram_inline_minerva_insight",
      topicKey: event.topicKey,
      title: event.title,
      summary:
        `다음 주제에 대해 Minerva 2차적 사고 기반 인사이트 분석을 수행하세요.\n` +
        `- 핵심 변화(1차): ${event.summary}\n` +
        `- 2차 분석: 원인-결과 연결고리, 파급 영향도, 리스크/기회 분해\n` +
        `- 요청 출력: 우선순위 액션 3개`,
      sourceRefs: (event.sourceRefs ?? []).map((item) => ({ title: item.title, url: item.url })),
    });
    await answerTelegramCallback({
      callbackQueryId: callback.id,
      text: "Minerva 2차 인사이트 분석 요청을 접수했습니다.",
    });
    return NextResponse.json({
      ok: true,
      mode: "callback_query",
      action: parsed.action,
      eventId: event.eventId,
      inbox: result,
    });
  }

  await answerTelegramCallback({ callbackQueryId: callback.id, text: "지원하지 않는 액션입니다." });
  return NextResponse.json({ ok: true, ignored: true, reason: "unsupported_action" });
}

async function handleTextMessageUpdate(message: TelegramMessage, request: NextRequest) {
  const text = (message.text ?? "").trim();
  if (!text) {
    return NextResponse.json({ ok: true, ignored: true, reason: "empty_message_text" });
  }

  const allowlist = verifyAllowlist({ from: message.from, chat: message.chat });
  if (!allowlist.ok) {
    return NextResponse.json({ error: "forbidden_message_source", reason: allowlist.reason }, { status: 403 });
  }

  if (text === "/start" || text === "/help") {
    const helpText =
      "🤝 Minerva 대화 모드입니다.\n\n" +
      "• 일반 메시지를 보내면 Minerva가 답변합니다.\n" +
      "• /reset 으로 대화 히스토리를 초기화할 수 있습니다.\n" +
      "• 인라인 버튼은 브리핑 메시지 하단에서 사용할 수 있습니다.";
    const sendResult = await sendTelegramTextMessage({ chatId: allowlist.chatId, text: helpText });
    return NextResponse.json({
      ok: true,
      mode: "message_text",
      command: text,
      chatId: allowlist.chatId,
      telegram: sendResult,
    });
  }

  if (text === "/reset") {
    await clearTelegramChatHistory(allowlist.chatId);
    const sendResult = await sendTelegramTextMessage({
      chatId: allowlist.chatId,
      text: "🧹 Minerva 대화 컨텍스트를 초기화했습니다.",
    });
    return NextResponse.json({
      ok: true,
      mode: "message_text",
      command: text,
      chatId: allowlist.chatId,
      telegram: sendResult,
    });
  }

  const rate = checkTextRateLimit(allowlist.chatId);
  if (!rate.ok) {
    const sendResult = await sendTelegramTextMessage({
      chatId: allowlist.chatId,
      text: `요청이 많아 잠시 제한합니다. ${rate.retryAfterSec}초 후 다시 시도해 주세요.`,
    });
    return NextResponse.json(
      {
        ok: false,
        mode: "message_text",
        error: "rate_limited",
        retryAfterSec: rate.retryAfterSec,
        telegram: sendResult,
      },
      { status: 429 }
    );
  }

  const historyLimit = parseIntEnv("TELEGRAM_MINERVA_HISTORY_TURNS", 10, 1);
  const maxHistoryEntries = Math.max(4, historyLimit * 2);
  const history = await getTelegramChatHistory(allowlist.chatId, maxHistoryEntries);

  let reply = "";
  let model: string | undefined;
  let minervaError: string | null = null;

  try {
    const result = await callMinervaChat({
      request,
      message: text,
      history,
    });
    reply = result.reply;
    model = result.model;
  } catch (error) {
    minervaError = error instanceof Error ? error.message : "unknown_error";
    reply = "현재 Minerva 응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.";
  }

  await appendTelegramChatHistory({
    chatId: allowlist.chatId,
    userText: compactLine(text, 1200),
    assistantText: compactLine(reply, 2400),
    maxEntries: maxHistoryEntries,
  });

  const sendResult = await sendTelegramTextMessage({
    chatId: allowlist.chatId,
    text: reply,
  });

  return NextResponse.json({
    ok: true,
    mode: "message_text",
    chatId: allowlist.chatId,
    userId: allowlist.userId,
    model: model ?? null,
    minerva: {
      ok: minervaError === null,
      error: minervaError,
    },
    telegram: sendResult,
  });
}

export async function POST(request: NextRequest) {
  if (!verifyWebhookSecret(request)) {
    return NextResponse.json({ error: "unauthorized_webhook" }, { status: 401 });
  }

  const update = (await request.json()) as TelegramUpdate;
  if (update.callback_query) {
    return handleCallbackUpdate(update.callback_query);
  }
  if (update.message?.text) {
    return handleTextMessageUpdate(update.message, request);
  }
  return NextResponse.json({ ok: true, ignored: true, reason: "unsupported_update_type" });
}
