import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import type { CanonicalAgentId } from "@/lib/agents";
import {
  appendCompactMemoryFromEvent,
  appendCompactMemoryFromTelegram,
} from "@/lib/orchestration/compact-memory";
import { getApprovalPolicy } from "@/lib/orchestration/policy";
import { AgentEvent } from "@/lib/orchestration/types";

type CooldownState = Record<string, string>;

type DigestBucket = {
  slot: string;
  items: AgentEvent[];
  updatedAt: string;
};

type TelegramChatRole = "user" | "assistant";
export type TelegramApprovalAction = "clio_save" | "hermes_deep_dive" | "minerva_insight";
export type TelegramApprovalStatus = "pending_step1" | "pending_step2" | "approved" | "rejected" | "expired";

export type TelegramChatHistoryEntry = {
  role: TelegramChatRole;
  text: string;
  at: string;
};

type TelegramChatHistoryStore = Record<string, TelegramChatHistoryEntry[]>;
type TelegramApprovalStore = Record<string, TelegramPendingApproval>;

export type TelegramPendingApproval = {
  approvalId: string;
  action: TelegramApprovalAction;
  eventId: string;
  userId: string;
  chatId: string;
  status: TelegramApprovalStatus;
  createdAt: string;
  updatedAt: string;
  expiresAt: string;
  resolvedReason?: string;
};

const ROOT = process.env.SHARED_ROOT_PATH?.trim() || path.join(process.cwd(), "shared_data");
const MEMORY_DIR = path.join(ROOT, "shared_memory");
const AGENT_MEMORY_DIR = path.join(MEMORY_DIR, "agent_memory");
const LOCK_DIR = path.join(MEMORY_DIR, ".locks");
const EVENTS_FILE = path.join(MEMORY_DIR, "agent_events.json");
const COOLDOWN_FILE = path.join(MEMORY_DIR, "topic_cooldowns.json");
const DIGEST_FILE = path.join(MEMORY_DIR, "digest_queue.json");
const TELEGRAM_CHAT_HISTORY_FILE = path.join(MEMORY_DIR, "telegram_chat_history.json");
const TELEGRAM_APPROVAL_FILE = path.join(MEMORY_DIR, "telegram_pending_approvals.json");
const MEMORY_MARKDOWN_FILE = path.join(MEMORY_DIR, "memory.md");
const MEMORY_MARKDOWN_MAX_BYTES = Math.max(32_000, Number(process.env.MEMORY_MD_MAX_BYTES ?? 280_000) || 280_000);
const MEMORY_MARKDOWN_HEADER = [
  "# NanoClaw Runtime Memory",
  "",
  "- Purpose: shared runtime log for Minerva/Clio/Hermes orchestration.",
  "- Source: generated automatically from event + telegram chat paths.",
  "- Retention: auto-rotated when file exceeds size limit.",
  "",
  "## Timeline",
  "",
].join("\n");

const AGENT_MEMORY_MARKDOWN_MAX_BYTES = Math.max(
  16_000,
  Number(process.env.AGENT_MEMORY_MD_MAX_BYTES ?? 120_000) || 120_000
);
const MEMORY_SKIP_TAGS = new Set(
  (process.env.MEMORY_SKIP_TAGS ?? "verification,rehearsal,test,smoke")
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter((item) => item.length > 0)
);

const STORAGE_LOCK_TIMEOUT_MS = Math.max(500, Number(process.env.STORAGE_LOCK_TIMEOUT_MS ?? 4000) || 4000);
const STORAGE_LOCK_STALE_MS = Math.max(1000, Number(process.env.STORAGE_LOCK_STALE_MS ?? 15000) || 15000);
const STORAGE_LOCK_RETRY_MS = Math.max(15, Number(process.env.STORAGE_LOCK_RETRY_MS ?? 60) || 60);

async function ensureMemoryDir() {
  await fs.mkdir(MEMORY_DIR, { recursive: true });
}

function sanitizeLockName(name: string) {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "_")
    .replace(/^_+|_+$/g, "") || "storage";
}

async function sleepMs(ms: number) {
  await new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });
}

async function withNamedLock<T>(name: string, fn: () => Promise<T>): Promise<T> {
  await ensureMemoryDir();
  await fs.mkdir(LOCK_DIR, { recursive: true });
  const lockPath = path.join(LOCK_DIR, `${sanitizeLockName(name)}.lock`);
  const deadline = Date.now() + STORAGE_LOCK_TIMEOUT_MS;

  while (true) {
    try {
      await fs.mkdir(lockPath);
      break;
    } catch (error) {
      const err = error as NodeJS.ErrnoException;
      if (err.code !== "EEXIST") {
        throw error;
      }
      try {
        const stat = await fs.stat(lockPath);
        if (Date.now() - stat.mtimeMs > STORAGE_LOCK_STALE_MS) {
          await fs.rm(lockPath, { recursive: true, force: true });
          continue;
        }
      } catch {
        // Lock disappeared while checking stale age.
      }

      if (Date.now() >= deadline) {
        throw new Error(`storage_lock_timeout:${name}`);
      }
      await sleepMs(STORAGE_LOCK_RETRY_MS);
    }
  }

  try {
    return await fn();
  } finally {
    await fs.rm(lockPath, { recursive: true, force: true }).catch(() => undefined);
  }
}

async function ensureMemoryMarkdownFile() {
  await ensureMemoryDir();
  try {
    await fs.access(MEMORY_MARKDOWN_FILE);
  } catch {
    await fs.writeFile(MEMORY_MARKDOWN_FILE, MEMORY_MARKDOWN_HEADER, "utf-8");
  }
}

function agentMemoryHeader(agentId: CanonicalAgentId) {
  return [
    `# ${agentId} Runtime Memory`,
    "",
    "- Purpose: compact per-agent memory for low-token context injection.",
    "- Source: generated from orchestration events and telegram chat bridge.",
    "",
    "## Timeline",
    "",
  ].join("\n");
}

function getAgentRuntimeMemoryPath(agentId: CanonicalAgentId) {
  return path.join(AGENT_MEMORY_DIR, `${agentId}.md`);
}

async function ensureAgentMemoryMarkdownFile(agentId: CanonicalAgentId) {
  await ensureMemoryDir();
  await fs.mkdir(AGENT_MEMORY_DIR, { recursive: true });
  const target = getAgentRuntimeMemoryPath(agentId);
  try {
    await fs.access(target);
  } catch {
    await fs.writeFile(target, agentMemoryHeader(agentId), "utf-8");
  }
  return target;
}

async function readJsonFile<T>(filePath: string, fallback: T): Promise<T> {
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

async function writeJsonFile(filePath: string, payload: unknown) {
  await ensureMemoryDir();
  const tmpPath = `${filePath}.${process.pid}.${crypto.randomUUID().slice(0, 8)}.tmp`;
  await fs.writeFile(tmpPath, JSON.stringify(payload, null, 2), "utf-8");
  await fs.rename(tmpPath, filePath);
}

function singleLine(value: string | undefined, maxLen: number) {
  const compact = String(value ?? "")
    .replace(/\s+/g, " ")
    .replace(/\|/g, "\\|")
    .trim();
  if (!compact) {
    return "-";
  }
  if (compact.length <= maxLen) {
    return compact;
  }
  return `${compact.slice(0, Math.max(8, maxLen - 1)).trimEnd()}…`;
}

function shouldSkipEventForMemory(event: AgentEvent) {
  const tags = new Set((event.tags ?? []).map((item) => item.trim().toLowerCase()).filter((item) => item.length > 0));
  for (const tag of tags) {
    if (MEMORY_SKIP_TAGS.has(tag)) {
      return true;
    }
  }

  const haystack = `${event.title}\n${event.topicKey}\n${event.summary}`.toLowerCase();
  if (/\b(memory[-\s]?md|verification|rehearsal|smoke(\s|-)?test|healthcheck|heartbeat)\b/.test(haystack)) {
    return true;
  }

  // Hermes dispatch fallback duplicate: no source refs + generic summary.
  if (
    event.agentId === "hermes" &&
    (event.sourceRefs?.length ?? 0) === 0 &&
    /^total=\d+,\s*hot=\d+,\s*insight=\d+,\s*monitor=\d+$/i.test(event.summary.trim())
  ) {
    return true;
  }

  return false;
}

async function rotateMarkdownIfNeeded(filePath: string, header: string, maxBytes: number) {
  try {
    const stat = await fs.stat(filePath);
    if (stat.size <= maxBytes) {
      return;
    }
    const raw = await fs.readFile(filePath, "utf-8");
    const lines = raw.split("\n");
    const tail = lines.slice(-260);
    const rebuilt = [
      header.trimEnd(),
      "",
      `> rotated_at: ${new Date().toISOString()}`,
      "",
      ...tail,
      "",
    ].join("\n");
    await fs.writeFile(filePath, rebuilt, "utf-8");
  } catch {
    // Non-blocking: memory rotation should not fail core orchestration paths.
  }
}

async function appendMemoryMarkdownBlock(lines: string[]) {
  await ensureMemoryMarkdownFile();
  const payload = `${lines.join("\n")}\n\n`;
  await fs.appendFile(MEMORY_MARKDOWN_FILE, payload, "utf-8");
  await rotateMarkdownIfNeeded(MEMORY_MARKDOWN_FILE, MEMORY_MARKDOWN_HEADER, MEMORY_MARKDOWN_MAX_BYTES);
}

async function appendAgentMemoryMarkdownBlock(agentId: CanonicalAgentId, lines: string[]) {
  const target = await ensureAgentMemoryMarkdownFile(agentId);
  const payload = `${lines.join("\n")}\n\n`;
  await fs.appendFile(target, payload, "utf-8");
  await rotateMarkdownIfNeeded(target, agentMemoryHeader(agentId), AGENT_MEMORY_MARKDOWN_MAX_BYTES);
}

async function appendEventToMemoryMarkdown(event: AgentEvent) {
  if (shouldSkipEventForMemory(event)) {
    return;
  }
  const tags = (event.tags ?? []).map((item) => singleLine(item, 32)).filter((item) => item !== "-");
  const refs = (event.sourceRefs ?? []).slice(0, 3);
  const sourceLines =
    refs.length > 0
      ? refs.map((ref) => `- source: ${singleLine(ref.title, 80)} | ${singleLine(ref.url, 180)}`)
      : ["- source: -"];

  const lines = [
    `### ${event.createdAt} [${event.agentId}] ${singleLine(event.title, 120)}`,
    `- event_id: ${event.eventId}`,
    `- topic: ${singleLine(event.topicKey, 80)}`,
    `- priority_confidence: ${event.priority}/${Number(event.confidence).toFixed(2)}`,
    `- tags: ${tags.length > 0 ? tags.join(", ") : "-"}`,
    `- summary: ${singleLine(event.summary, 220)}`,
    ...sourceLines,
  ];
  await appendMemoryMarkdownBlock(lines);
  await appendAgentMemoryMarkdownBlock(event.agentId, lines);
}

async function appendTelegramTurnToMemoryMarkdown(params: {
  chatId: string;
  userText: string;
  assistantText: string;
  at: string;
}) {
  const lines = [
    `### ${params.at} [telegram][chat:${singleLine(params.chatId, 48)}]`,
    `- user: ${singleLine(params.userText, 180)}`,
    `- minerva: ${singleLine(params.assistantText, 220)}`,
  ];
  await appendMemoryMarkdownBlock(lines);
  await appendAgentMemoryMarkdownBlock("minerva", lines);
}

export function createEventId() {
  return crypto.randomUUID();
}

export function makeDedupeKey(topicKey: string, summary: string) {
  return crypto.createHash("sha256").update(`${topicKey}::${summary}`).digest("hex").slice(0, 20);
}

function isTerminalApprovalStatus(status: TelegramApprovalStatus) {
  return status === "approved" || status === "rejected" || status === "expired";
}

function normalizeApprovalStore(raw: TelegramApprovalStore): TelegramApprovalStore {
  const nowMs = Date.now();
  const nowIso = new Date(nowMs).toISOString();
  const next: TelegramApprovalStore = {};

  for (const [key, item] of Object.entries(raw ?? {})) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const expiresAtMs = new Date(item.expiresAt).getTime();
    const updatedAtMs = new Date(item.updatedAt).getTime();
    const expiresAtValid = Number.isFinite(expiresAtMs);
    const updatedAtValid = Number.isFinite(updatedAtMs);
    const status = item.status;
    if (status !== "pending_step1" && status !== "pending_step2" && status !== "approved" && status !== "rejected" && status !== "expired") {
      continue;
    }

    const normalized: TelegramPendingApproval = {
      ...item,
      approvalId: item.approvalId || key,
      updatedAt: updatedAtValid ? item.updatedAt : nowIso,
      expiresAt: expiresAtValid ? item.expiresAt : new Date(nowMs).toISOString(),
    };

    if (
      (normalized.status === "pending_step1" || normalized.status === "pending_step2") &&
      new Date(normalized.expiresAt).getTime() <= nowMs
    ) {
      normalized.status = "expired";
      normalized.updatedAt = nowIso;
      normalized.resolvedReason = normalized.resolvedReason || "expired_ttl";
    }

    // Keep terminal approvals for 24h to preserve auditability, then prune.
    if (
      isTerminalApprovalStatus(normalized.status) &&
      new Date(normalized.updatedAt).getTime() + 24 * 60 * 60 * 1000 < nowMs
    ) {
      continue;
    }
    next[key] = normalized;
  }
  return next;
}

async function readApprovalStore(): Promise<TelegramApprovalStore> {
  const raw = await readJsonFile<TelegramApprovalStore>(TELEGRAM_APPROVAL_FILE, {});
  return normalizeApprovalStore(raw);
}

async function writeApprovalStore(payload: TelegramApprovalStore) {
  await writeJsonFile(TELEGRAM_APPROVAL_FILE, normalizeApprovalStore(payload));
}

export async function createTelegramPendingApproval(params: {
  action: TelegramApprovalAction;
  eventId: string;
  userId: string;
  chatId: string;
  ttlSec?: number;
}) {
  const ttlSec = params.ttlSec ?? getApprovalPolicy().ttlSec;
  const now = new Date();
  const approvalId = crypto.randomBytes(8).toString("hex");
  const createdAt = now.toISOString();
  const expiresAt = new Date(now.getTime() + ttlSec * 1000).toISOString();
  const next: TelegramPendingApproval = {
    approvalId,
    action: params.action,
    eventId: params.eventId,
    userId: params.userId,
    chatId: params.chatId,
    status: "pending_step1",
    createdAt,
    updatedAt: createdAt,
    expiresAt,
  };

  await withNamedLock("telegram_approvals", async () => {
    const approvals = await readApprovalStore();
    approvals[approvalId] = next;
    await writeApprovalStore(approvals);
  });
  return next;
}

export async function findTelegramPendingApproval(approvalId: string) {
  const approvals = await readApprovalStore();
  return approvals[approvalId] ?? null;
}

export async function listTelegramPendingApprovals(params?: {
  chatId?: string;
  userId?: string;
  statuses?: TelegramApprovalStatus[];
}) {
  const approvals = await readApprovalStore();
  const targetStatuses = params?.statuses?.length ? new Set(params.statuses) : null;
  return Object.values(approvals)
    .filter((item) => {
      if (!item) {
        return false;
      }
      if (params?.chatId && item.chatId !== params.chatId) {
        return false;
      }
      if (params?.userId && item.userId !== params.userId) {
        return false;
      }
      if (targetStatuses && !targetStatuses.has(item.status)) {
        return false;
      }
      return true;
    })
    .sort((a, b) => new Date(a.expiresAt).getTime() - new Date(b.expiresAt).getTime());
}

export async function updateTelegramPendingApproval(params: {
  approvalId: string;
  status: TelegramApprovalStatus;
  resolvedReason?: string;
}) {
  return withNamedLock("telegram_approvals", async () => {
    const approvals = await readApprovalStore();
    const target = approvals[params.approvalId];
    if (!target) {
      return null;
    }
    const updated: TelegramPendingApproval = {
      ...target,
      status: params.status,
      updatedAt: new Date().toISOString(),
      resolvedReason: params.resolvedReason ?? target.resolvedReason,
    };
    approvals[params.approvalId] = updated;
    await writeApprovalStore(approvals);
    return updated;
  });
}

export async function appendAgentEvent(event: AgentEvent) {
  await withNamedLock("agent_events", async () => {
    const events = await readJsonFile<AgentEvent[]>(EVENTS_FILE, []);
    events.push(event);
    const capped = events.slice(-3000);
    await writeJsonFile(EVENTS_FILE, capped);
  });
  try {
    await appendCompactMemoryFromEvent(event);
  } catch {
    // Non-blocking: compact memory should not fail event ingestion.
  }
  try {
    await appendEventToMemoryMarkdown(event);
  } catch {
    // Non-blocking: memory markdown logging should not fail event ingestion.
  }
}

export async function listAgentEvents() {
  return readJsonFile<AgentEvent[]>(EVENTS_FILE, []);
}

export async function findEventById(eventId: string) {
  const events = await listAgentEvents();
  return events.find((item) => item.eventId === eventId) ?? null;
}

export async function getCooldown(topicKey: string) {
  const cooldowns = await readJsonFile<CooldownState>(COOLDOWN_FILE, {});
  return cooldowns[topicKey] ?? null;
}

export async function setCooldown(topicKey: string, untilIso: string) {
  await withNamedLock("topic_cooldowns", async () => {
    const cooldowns = await readJsonFile<CooldownState>(COOLDOWN_FILE, {});
    cooldowns[topicKey] = untilIso;
    await writeJsonFile(COOLDOWN_FILE, cooldowns);
  });
}

export async function pushDigestItem(slot: string, event: AgentEvent) {
  await withNamedLock("digest_queue", async () => {
    const queue = await readJsonFile<Record<string, DigestBucket>>(DIGEST_FILE, {});
    const bucket = queue[slot] ?? { slot, items: [], updatedAt: new Date().toISOString() };
    bucket.items.push(event);
    bucket.updatedAt = new Date().toISOString();
    queue[slot] = {
      ...bucket,
      items: bucket.items.slice(-200),
    };
    await writeJsonFile(DIGEST_FILE, queue);
  });
}

export async function createInboxTask(params: {
  targetAgentId: "minerva" | "clio" | "hermes";
  reason: string;
  topicKey: string;
  title: string;
  summary: string;
  sourceRefs: Array<{ title: string; url: string }>;
}) {
  const inboxDir = path.join(ROOT, "inbox");
  await fs.mkdir(inboxDir, { recursive: true });

  const now = new Date().toISOString();
  const stamp = now.replace(/[:.]/g, "-");
  const fileName = `${stamp}-${params.targetAgentId}-${crypto.randomUUID().slice(0, 8)}.json`;
  const targetPath = path.join(inboxDir, fileName);

  const lines = [
    `[trigger] ${params.reason}`,
    `[topic] ${params.topicKey}`,
    `[title] ${params.title}`,
    "",
    params.summary,
    "",
    "[sources]",
    ...params.sourceRefs.map((source) => `- ${source.title}: ${source.url}`),
  ];

  const payload = {
    agent_id: params.targetAgentId,
    source: "telegram-inline-action",
    message: lines.join("\n"),
    triggered_at: now,
  };
  await fs.writeFile(targetPath, JSON.stringify(payload, null, 2), "utf-8");
  return {
    inboxFile: fileName,
    path: targetPath,
  };
}

export async function getTelegramChatHistory(chatId: string, limit = 12): Promise<TelegramChatHistoryEntry[]> {
  const history = await readJsonFile<TelegramChatHistoryStore>(TELEGRAM_CHAT_HISTORY_FILE, {});
  const rows = history[chatId] ?? [];
  if (!Array.isArray(rows)) {
    return [];
  }
  const normalized = rows.filter((entry) => {
    if (!entry || typeof entry !== "object") {
      return false;
    }
    if (entry.role !== "user" && entry.role !== "assistant") {
      return false;
    }
    return typeof entry.text === "string" && entry.text.trim().length > 0;
  });
  return normalized.slice(-Math.max(1, limit));
}

export async function appendTelegramChatHistory(params: {
  chatId: string;
  userText: string;
  assistantText: string;
  maxEntries?: number;
}) {
  const maxEntries = Math.max(4, params.maxEntries ?? 24);
  const now = new Date().toISOString();
  await withNamedLock("telegram_chat_history", async () => {
    const history = await readJsonFile<TelegramChatHistoryStore>(TELEGRAM_CHAT_HISTORY_FILE, {});
    const current = Array.isArray(history[params.chatId]) ? history[params.chatId] : [];
    const next = [
      ...current,
      { role: "user" as const, text: params.userText, at: now },
      { role: "assistant" as const, text: params.assistantText, at: now },
    ].slice(-maxEntries);
    history[params.chatId] = next;
    await writeJsonFile(TELEGRAM_CHAT_HISTORY_FILE, history);
  });
  try {
    await appendCompactMemoryFromTelegram({
      chatId: params.chatId,
      userText: params.userText,
      assistantText: params.assistantText,
      at: now,
    });
  } catch {
    // Non-blocking: compact memory should not fail chat persistence.
  }
  try {
    await appendTelegramTurnToMemoryMarkdown({
      chatId: params.chatId,
      userText: params.userText,
      assistantText: params.assistantText,
      at: now,
    });
  } catch {
    // Non-blocking: memory markdown logging should not fail chat persistence.
  }
}

export async function clearTelegramChatHistory(chatId: string) {
  await withNamedLock("telegram_chat_history", async () => {
    const history = await readJsonFile<TelegramChatHistoryStore>(TELEGRAM_CHAT_HISTORY_FILE, {});
    if (history[chatId]) {
      delete history[chatId];
      await writeJsonFile(TELEGRAM_CHAT_HISTORY_FILE, history);
    }
  });
}

export async function ensureRuntimeMemoryMarkdown() {
  await ensureMemoryMarkdownFile();
}

export function getRuntimeMemoryMarkdownPath() {
  return MEMORY_MARKDOWN_FILE;
}

export function getAgentMemoryMarkdownPath(agentId: CanonicalAgentId) {
  return getAgentRuntimeMemoryPath(agentId);
}
