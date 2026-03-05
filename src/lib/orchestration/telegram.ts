import {
  AgentEvent,
  MinervaCalendarBriefing,
  TelegramDispatchPayload,
  TelegramInlineKeyboard,
} from "@/lib/orchestration/types";
import { sourceCategoryEmoji, sourceCategoryLabel } from "@/lib/orchestration/source-taxonomy";
import { shouldTranslateToKorean, translateToKorean } from "@/lib/integrations/deepl";
import type { ApprovalAction, ApprovalRecord } from "@/lib/orchestration/storage";

const PRIORITY_EMOJI: Record<AgentEvent["priority"], string> = {
  critical: "🚨",
  high: "🔔",
  normal: "🧭",
  low: "📝",
};

type BriefingTier = "P0" | "P1" | "P2";

type TierStyle = {
  header: string;
  summaryMaxLines: number;
  insightMaxLines: number;
  maxSources: number;
};

const TIER_STYLES: Record<BriefingTier, TierStyle> = {
  P0: {
    header: "⚡ P0 즉시 브리핑",
    summaryMaxLines: 2,
    insightMaxLines: 1,
    maxSources: 2,
  },
  P1: {
    header: "🧠 P1 분석 브리핑",
    summaryMaxLines: 3,
    insightMaxLines: 2,
    maxSources: 3,
  },
  P2: {
    header: "🗂️ P2 스캔 브리핑",
    summaryMaxLines: 2,
    insightMaxLines: 2,
    maxSources: 2,
  },
};

type TranslationTierPolicy = {
  translateSummary: boolean;
  summaryCharLimit: number;
  maxSnippetTranslations: number;
  snippetCharLimit: number;
};

const TIER_TRANSLATION_POLICY: Record<BriefingTier, TranslationTierPolicy> = {
  // P0는 즉시 판단이 필요하므로 핵심요약 + 상위 2개 스니펫까지 번역
  P0: { translateSummary: true, summaryCharLimit: 480, maxSnippetTranslations: 2, snippetCharLimit: 180 },
  // P1은 비용을 줄이기 위해 핵심요약 + 상위 1개 스니펫만 번역
  P1: { translateSummary: true, summaryCharLimit: 420, maxSnippetTranslations: 1, snippetCharLimit: 160 },
  // P2는 스캔/관찰 목적이라 자동 번역을 비활성화
  P2: { translateSummary: false, summaryCharLimit: 0, maxSnippetTranslations: 0, snippetCharLimit: 0 },
};

function cleanLine(value: string): string {
  return value
    .replace(/\r/g, "")
    .replace(/\\n/g, "\n")
    .replace(/\*\*/g, "")
    .replace(/^\s{0,3}#{1,6}\s*/g, "")
    .replace(/^["“”'`]+|["“”'`]+$/g, "")
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

function normalizeTier(value: unknown): BriefingTier | null {
  if (typeof value !== "string") {
    return null;
  }
  const token = value.trim().toUpperCase();
  if (token === "P0" || token === "P1" || token === "P2") {
    return token;
  }
  return null;
}

function tierRank(value: BriefingTier): number {
  if (value === "P0") {
    return 0;
  }
  if (value === "P1") {
    return 1;
  }
  return 2;
}

function inferTier(event: AgentEvent): BriefingTier {
  const tagTier = (event.tags ?? [])
    .map((token) => {
      const match = token.match(/^tier:(p[0-2])$/i);
      return match ? normalizeTier(match[1]) : null;
    })
    .find((tier): tier is BriefingTier => tier !== null);
  if (tagTier) {
    return tagTier;
  }

  const payloadTier = normalizeTier(event.payload?.["priority_tier"]);
  const sourceTiers = (event.sourceRefs ?? [])
    .map((source) => normalizeTier(source.priorityTier))
    .filter((tier): tier is BriefingTier => tier !== null);
  const allCandidates = [...sourceTiers];
  if (payloadTier) {
    allCandidates.push(payloadTier);
  }
  if (allCandidates.length > 0) {
    return allCandidates.reduce((best, next) => (tierRank(next) < tierRank(best) ? next : best), allCandidates[0]);
  }

  if (event.priority === "critical") {
    return "P0";
  }
  if (event.priority === "high") {
    return "P1";
  }
  return "P2";
}

type TopicEntry = {
  title: string;
  url: string;
  snippet?: string;
  tier: BriefingTier;
  categoryLabel: string;
  emoji: string;
};

export function createInlineKeyboard(eventId: string): TelegramInlineKeyboard {
  return {
    inline_keyboard: [
      [{ text: "Clio, 옵시디언에 저장해", callback_data: `clio_save:${eventId}` }],
      [{ text: "Hermes, 더 찾아", callback_data: `hermes_deep_dive:${eventId}` }],
      [{ text: "Minerva, 인사이트 분석해", callback_data: `minerva_insight:${eventId}` }],
    ],
  };
}

function approvalActionLabel(action: ApprovalAction): string {
  if (action === "clio_save") {
    return "Clio 저장";
  }
  if (action === "hermes_deep_dive") {
    return "Hermes 추가 수집";
  }
  return "Minerva 인사이트 분석";
}

export function createApprovalStage1Keyboard(approvalId: string): TelegramInlineKeyboard {
  return {
    inline_keyboard: [
      [
        { text: "네, 진행", callback_data: `approval_yes:${approvalId}` },
        { text: "아니요", callback_data: `approval_no:${approvalId}` },
      ],
    ],
  };
}

export function createApprovalStage2Keyboard(approvalId: string): TelegramInlineKeyboard {
  return {
    inline_keyboard: [
      [
        { text: "최종 승인", callback_data: `approval_commit:${approvalId}` },
        { text: "취소", callback_data: `approval_no:${approvalId}` },
      ],
    ],
  };
}

export function renderApprovalStage1Text(approval: ApprovalRecord): string {
  return trimTelegramText(
    [
      "⚠️ 승인 필요",
      "",
      `- 액션: ${approvalActionLabel(approval.action)}`,
      `- 주제: ${shortText(approval.topicKey, 70)}`,
      `- 제목: ${shortText(approval.eventTitle, 72)}`,
      "- 1차 확인: 아래 버튼으로 승인 또는 취소를 선택하세요.",
      `- 만료: ${shortText(approval.expiresAt, 36)}`,
    ].join("\n"),
    1200
  );
}

export function renderApprovalStage2Text(approval: ApprovalRecord): string {
  return trimTelegramText(
    [
      "⚠️ 최종 승인 필요",
      "",
      `- 액션: ${approvalActionLabel(approval.action)}`,
      `- 주제: ${shortText(approval.topicKey, 70)}`,
      `- 제목: ${shortText(approval.eventTitle, 72)}`,
      "- 실수 방지: 정말 진행할지 한 번 더 확인하세요.",
      `- 만료: ${shortText(approval.expiresAt, 36)}`,
    ].join("\n"),
    1200
  );
}

function buildCalendarLines(calendarBriefing?: MinervaCalendarBriefing | null): string[] {
  if (!calendarBriefing) {
    return [];
  }
  const lines = [`오늘 일정: ${shortText(calendarBriefing.summary, 120)}`];
  if (calendarBriefing.items.length > 0) {
    lines.push(...calendarBriefing.items.slice(0, 3).map((item) => `- ${item.timeLabel} ${shortText(item.title, 56)}`));
  }
  return lines;
}

function summaryLines(value: string, maxLines: number): string[] {
  const lines = value
    .replace(/\r/g, "")
    .replace(/\\n/g, "\n")
    .replace(/\*\*/g, "")
    .split("\n")
    .map((line) => line.replace(/^\s{0,3}#{1,6}\s*/g, "").trim())
    .map((line) => shortText(line, 140))
    .filter((line) => line.length > 0);
  return lines.slice(0, maxLines);
}

function trimTelegramText(value: string, maxLen = 3700): string {
  const normalized = value.replace(/\n{3,}/g, "\n\n").trim();
  if (normalized.length <= maxLen) {
    return normalized;
  }
  return `${normalized.slice(0, maxLen - 1).trimEnd()}…`;
}

function trimForTranslation(value: string, limit: number): string {
  const normalized = cleanLine(value);
  if (limit <= 0 || normalized.length <= limit) {
    return normalized;
  }
  return `${normalized.slice(0, limit - 1).trimEnd()}…`;
}

function buildTopics(event: AgentEvent, tier: BriefingTier, maxTopics: number): TopicEntry[] {
  const refs = event.sourceRefs ?? [];
  const seen = new Set<string>();
  const topics: TopicEntry[] = [];

  for (const ref of refs) {
    const key = `${ref.url}|${ref.title}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    topics.push({
      title: ref.title,
      url: ref.url,
      snippet: ref.snippet,
      tier: normalizeTier(ref.priorityTier) ?? tier,
      categoryLabel: sourceCategoryLabel(ref.category),
      emoji: sourceCategoryEmoji(ref.category),
    });
    if (topics.length >= maxTopics) {
      break;
    }
  }

  if (topics.length > 0) {
    return topics;
  }

  return [
    {
      title: event.title,
      url: "",
      snippet: event.summary,
      tier,
      categoryLabel: "Uncategorized",
      emoji: "📎",
    },
  ];
}

function buildTopicLines(topics: TopicEntry[]): string[] {
  return topics.map((topic) => `- ${topic.emoji} ${shortText(topic.title, 64)}`);
}

function buildSummaryByTopic(event: AgentEvent, topics: TopicEntry[], maxLines: number): string[] {
  const fallback = summaryLines(event.summary, Math.max(1, maxLines));
  if (topics.length <= 1) {
    if (topics.length === 1 && topics[0].snippet) {
      return [`- ${shortText(cleanLine(topics[0].snippet), 140)}`];
    }
    return fallback.length > 0 ? fallback.map((line) => `- ${line}`) : ["- 요약 없음"];
  }

  const rows: string[] = [];
  for (let i = 0; i < Math.min(maxLines, topics.length); i += 1) {
    const topic = topics[i];
    const summary = topic.snippet ? shortText(cleanLine(topic.snippet), 110) : fallback[i % Math.max(1, fallback.length)] || "요약 없음";
    rows.push(`- ${topic.emoji} ${shortText(topic.title, 32)}: ${summary}`);
  }
  return rows;
}

function buildSources(topics: TopicEntry[]): string[] {
  return topics.map((topic, index) => {
    if (!topic.url) {
      return `- ${index + 1}) [${topic.tier}] ${topic.emoji} ${topic.categoryLabel} | 내부 이벤트`;
    }
    return `- ${index + 1}) [${topic.tier}] ${topic.emoji} ${topic.categoryLabel} | ${shortText(topic.title, 52)}\n  ${topic.url}`;
  });
}

function buildInsightSection(event: AgentEvent, style: TierStyle, topics: TopicEntry[]): string[] {
  const lines = summaryLines(
    event.insightHint || "연결 가능한 신호를 확인했고, 다음 액션 우선순위를 조정하는 것이 좋겠습니다.",
    style.insightMaxLines
  ).map((line) => `- ${line}`);

  const focusTopics = topics.slice(0, 2).map((topic) => `${topic.emoji} ${shortText(topic.title, 22)}`);
  if (focusTopics.length > 0) {
    lines.push(`- 우선 분석 대상: ${focusTopics.join(" / ")}`);
  }

  const uniqueCategories = Array.from(new Set(topics.map((topic) => `${topic.emoji} ${topic.categoryLabel}`)));
  if (uniqueCategories.length >= 2) {
    lines.push(`- 연관성: ${uniqueCategories.slice(0, 2).join(" ↔ ")} 축의 동시 변화가 보입니다.`);
  }
  return lines;
}

export function renderMinervaTelegramText(event: AgentEvent, calendarBriefing?: MinervaCalendarBriefing | null): string {
  const emoji = PRIORITY_EMOJI[event.priority];
  const tier = inferTier(event);
  const style = TIER_STYLES[tier];
  const topics = buildTopics(event, tier, style.maxSources);
  const summary = buildSummaryByTopic(event, topics, style.summaryMaxLines);
  const sources = buildSources(topics);
  const insight = buildInsightSection(event, style, topics);
  const calendarLines = buildCalendarLines(calendarBriefing);

  return trimTelegramText([
    `${emoji} Minerva 브리핑 · ${style.header}`,
    "",
    `🧩 주제`,
    ...buildTopicLines(topics),
    "",
    `📌 핵심 요약`,
    ...summary,
    ...(calendarLines.length > 0 ? ["", "📅 오늘 일정", ...calendarLines] : []),
    "",
    `🔎 출처`,
    ...sources,
    "",
    `🧠 Minerva 인사이트`,
    ...(insight.length > 0 ? insight : ["- 인사이트 힌트 없음"]),
  ].join("\n"));
}

async function localizeEventForTelegram(event: AgentEvent): Promise<AgentEvent> {
  const hasDeepLKey = Boolean((process.env.DEEPL_API_KEY ?? "").trim());
  if (!hasDeepLKey) {
    return event;
  }

  const tier = inferTier(event);
  const policy = TIER_TRANSLATION_POLICY[tier];
  if (!policy.translateSummary && policy.maxSnippetTranslations <= 0) {
    return event;
  }

  let translatedSummary = event.summary;
  if (policy.translateSummary) {
    const summaryCandidate = trimForTranslation(event.summary, policy.summaryCharLimit);
    if (shouldTranslateToKorean(summaryCandidate)) {
      translatedSummary = await translateToKorean(summaryCandidate);
    }
  }

  const translatedRefs: NonNullable<AgentEvent["sourceRefs"]> = [];
  let translatedSnippetCount = 0;
  for (const source of event.sourceRefs ?? []) {
    if (!source.snippet || translatedSnippetCount >= policy.maxSnippetTranslations) {
      translatedRefs.push(source);
      continue;
    }

    const snippetCandidate = trimForTranslation(source.snippet, policy.snippetCharLimit);
    if (!shouldTranslateToKorean(snippetCandidate)) {
      translatedRefs.push(source);
      continue;
    }

    translatedSnippetCount += 1;
    translatedRefs.push({
      ...source,
      snippet: await translateToKorean(snippetCandidate),
    });
  }

  return {
    ...event,
    summary: translatedSummary,
    sourceRefs: translatedRefs,
  };
}

export async function buildTelegramDispatchPayload(params: {
  chatId: string;
  event: AgentEvent;
  calendarBriefing?: MinervaCalendarBriefing | null;
}): Promise<TelegramDispatchPayload> {
  const localizedEvent = await localizeEventForTelegram(params.event);
  return {
    chat_id: params.chatId,
    text: renderMinervaTelegramText(localizedEvent, params.calendarBriefing),
    disable_web_page_preview: true,
    reply_markup: createInlineKeyboard(localizedEvent.eventId),
  };
}

async function postTelegramApi(method: "sendMessage" | "answerCallbackQuery", payload: object) {
  const token = process.env.TELEGRAM_BOT_TOKEN?.trim();
  if (!token) {
    return { ok: false, reason: "telegram_token_missing" as const };
  }
  const endpoint = `https://api.telegram.org/bot${token}/${method}`;
  let response: Response;
  try {
    response = await fetch(endpoint, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store",
    });
  } catch (error) {
    return {
      ok: false,
      reason: "telegram_api_failed" as const,
      detail: error instanceof Error ? error.message : "telegram_fetch_failed",
    };
  }
  if (!response.ok) {
    const body = await response.text();
    return {
      ok: false,
      reason: "telegram_api_failed" as const,
      status: response.status,
      detail: body,
    };
  }
  const body = await response.json();
  return { ok: true, reason: "ok" as const, response: body };
}

export async function sendTelegramMessage(payload: TelegramDispatchPayload) {
  const result = await postTelegramApi("sendMessage", payload);
  if (!result.ok) {
    return {
      sent: false,
      reason: result.reason === "telegram_token_missing" ? "telegram_token_missing" : "telegram_send_failed",
      status: "status" in result ? result.status : undefined,
      detail: "detail" in result ? result.detail : undefined,
    };
  }
  return { sent: true, reason: "ok" as const, response: result.response };
}

export async function sendTelegramTextMessage(params: {
  chatId: string;
  text: string;
  disableWebPagePreview?: boolean;
}) {
  return sendTelegramMessage({
    chat_id: params.chatId,
    text: params.text,
    disable_web_page_preview: params.disableWebPagePreview ?? true,
  });
}

export async function answerTelegramCallback(params: {
  callbackQueryId: string;
  text: string;
  showAlert?: boolean;
}) {
  const result = await postTelegramApi("answerCallbackQuery", {
    callback_query_id: params.callbackQueryId,
    text: shortText(params.text, 120),
    show_alert: Boolean(params.showAlert),
  });
  if (!result.ok) {
    return {
      ok: false,
      reason: result.reason === "telegram_token_missing" ? "telegram_token_missing" : "telegram_answer_failed",
      status: "status" in result ? result.status : undefined,
    };
  }
  return { ok: true };
}
