import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import type { CanonicalAgentId } from "@/lib/agents";
import {
  appendCompactMemoryFromEvent,
  appendCompactMemoryFromTelegram,
} from "@/lib/orchestration/compact-memory";
import { AgentEvent } from "@/lib/orchestration/types";

type CooldownState = Record<string, string>;

type DigestBucket = {
  slot: string;
  items: AgentEvent[];
  updatedAt: string;
};

type TelegramChatRole = "user" | "assistant";

export type TelegramChatHistoryEntry = {
  role: TelegramChatRole;
  text: string;
  at: string;
};

type TelegramChatHistoryStore = Record<string, TelegramChatHistoryEntry[]>;

export type ApprovalAction = "clio_save" | "hermes_deep_dive" | "minerva_insight";
export type ApprovalStatus =
  | "pending_stage1"
  | "pending_stage2"
  | "executed"
  | "rejected"
  | "expired";

export type ApprovalRecord = {
  id: string;
  action: ApprovalAction;
  eventId: string;
  eventTitle: string;
  topicKey: string;
  chatId: string;
  requestedByUserId: string;
  requestedAt: string;
  expiresAt: string;
  requiredSteps: 1 | 2;
  status: ApprovalStatus;
  history: Array<{
    at: string;
    type: "created" | "stage1_approved" | "executed" | "rejected" | "expired";
    actorUserId?: string;
  }>;
};

type ApprovalStore = {
  updatedAt: string;
  approvals: Record<string, ApprovalRecord>;
};

const ROOT = process.env.SHARED_ROOT_PATH?.trim() || path.join(process.cwd(), "shared_data");
const MEMORY_DIR = path.join(ROOT, "shared_memory");
const AGENT_MEMORY_DIR = path.join(MEMORY_DIR, "agent_memory");
const EVENTS_FILE = path.join(MEMORY_DIR, "agent_events.json");
const COOLDOWN_FILE = path.join(MEMORY_DIR, "topic_cooldowns.json");
const DIGEST_FILE = path.join(MEMORY_DIR, "digest_queue.json");
const TELEGRAM_CHAT_HISTORY_FILE = path.join(MEMORY_DIR, "telegram_chat_history.json");
const APPROVAL_QUEUE_FILE = path.join(MEMORY_DIR, "approval_queue.json");
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

function readPositiveInt(raw: string | undefined, fallback: number, minValue: number): number {
  if (!raw) {
    return fallback;
  }
  const parsed = Number(raw.trim());
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(minValue, Math.trunc(parsed));
}

const APPROVAL_TTL_SEC = readPositiveInt(process.env.TELEGRAM_APPROVAL_TTL_SEC, 300, 60);
const APPROVAL_RETENTION_HOURS = readPositiveInt(process.env.TELEGRAM_APPROVAL_RETENTION_HOURS, 72, 1);

async function ensureMemoryDir() {
  await fs.mkdir(MEMORY_DIR, { recursive: true });
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
  const tmpPath = `${filePath}.tmp`;
  await fs.writeFile(tmpPath, JSON.stringify(payload, null, 2), "utf-8");
  await fs.rename(tmpPath, filePath);
}

function defaultApprovalStore(): ApprovalStore {
  return {
    updatedAt: new Date().toISOString(),
    approvals: {},
  };
}

function createApprovalId() {
  return crypto.randomBytes(6).toString("hex");
}

function approvalIsPending(status: ApprovalStatus): boolean {
  return status === "pending_stage1" || status === "pending_stage2";
}

function pruneApprovalStore(store: ApprovalStore, now: Date): ApprovalStore {
  const retentionMs = APPROVAL_RETENTION_HOURS * 3600 * 1000;
  let dirty = false;

  for (const [id, approval] of Object.entries(store.approvals)) {
    const expiresAt = Date.parse(approval.expiresAt);
    if (approvalIsPending(approval.status) && Number.isFinite(expiresAt) && expiresAt <= now.getTime()) {
      store.approvals[id] = {
        ...approval,
        status: "expired",
        history: [...approval.history, { at: now.toISOString(), type: "expired" }],
      };
      dirty = true;
      continue;
    }

    const requestedAt = Date.parse(approval.requestedAt);
    if (!approvalIsPending(approval.status) && Number.isFinite(requestedAt) && now.getTime() - requestedAt > retentionMs) {
      delete store.approvals[id];
      dirty = true;
    }
  }

  if (dirty) {
    store.updatedAt = now.toISOString();
  }
  return store;
}

async function readApprovalStore(): Promise<ApprovalStore> {
  const raw = await readJsonFile<ApprovalStore>(APPROVAL_QUEUE_FILE, defaultApprovalStore());
  const safe: ApprovalStore = {
    updatedAt: typeof raw.updatedAt === "string" ? raw.updatedAt : new Date().toISOString(),
    approvals: raw && typeof raw.approvals === "object" && raw.approvals ? raw.approvals : {},
  };
  return pruneApprovalStore(safe, new Date());
}

async function writeApprovalStore(store: ApprovalStore) {
  store.updatedAt = new Date().toISOString();
  await writeJsonFile(APPROVAL_QUEUE_FILE, store);
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

export async function appendAgentEvent(event: AgentEvent) {
  const events = await readJsonFile<AgentEvent[]>(EVENTS_FILE, []);
  events.push(event);
  const capped = events.slice(-3000);
  await writeJsonFile(EVENTS_FILE, capped);
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
  const cooldowns = await readJsonFile<CooldownState>(COOLDOWN_FILE, {});
  cooldowns[topicKey] = untilIso;
  await writeJsonFile(COOLDOWN_FILE, cooldowns);
}

export async function pushDigestItem(slot: string, event: AgentEvent) {
  const queue = await readJsonFile<Record<string, DigestBucket>>(DIGEST_FILE, {});
  const bucket = queue[slot] ?? { slot, items: [], updatedAt: new Date().toISOString() };
  bucket.items.push(event);
  bucket.updatedAt = new Date().toISOString();
  queue[slot] = {
    ...bucket,
    items: bucket.items.slice(-200),
  };
  await writeJsonFile(DIGEST_FILE, queue);
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
    schema_version: 1,
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

function resolveApprovalRequiredSteps(): 1 | 2 {
  const raw = readPositiveInt(process.env.TELEGRAM_APPROVAL_REQUIRED_STEPS, 2, 1);
  return raw >= 2 ? 2 : 1;
}

export async function createApprovalRequest(params: {
  action: ApprovalAction;
  eventId: string;
  eventTitle: string;
  topicKey: string;
  chatId: string;
  requestedByUserId: string;
}): Promise<{ approval: ApprovalRecord; reused: boolean }> {
  const store = await readApprovalStore();
  const existing = Object.values(store.approvals).find(
    (item) =>
      item.action === params.action &&
      item.eventId === params.eventId &&
      item.chatId === params.chatId &&
      item.requestedByUserId === params.requestedByUserId &&
      approvalIsPending(item.status)
  );
  if (existing) {
    return { approval: existing, reused: true };
  }

  const now = new Date();
  const requiredSteps = resolveApprovalRequiredSteps();
  const approval: ApprovalRecord = {
    id: createApprovalId(),
    action: params.action,
    eventId: params.eventId,
    eventTitle: params.eventTitle,
    topicKey: params.topicKey,
    chatId: params.chatId,
    requestedByUserId: params.requestedByUserId,
    requestedAt: now.toISOString(),
    expiresAt: new Date(now.getTime() + APPROVAL_TTL_SEC * 1000).toISOString(),
    requiredSteps,
    status: "pending_stage1",
    history: [{ at: now.toISOString(), type: "created", actorUserId: params.requestedByUserId }],
  };
  store.approvals[approval.id] = approval;
  await writeApprovalStore(store);
  return { approval, reused: false };
}

export async function getApprovalRequest(approvalId: string): Promise<ApprovalRecord | null> {
  const store = await readApprovalStore();
  const found = store.approvals[approvalId];
  if (!found) {
    return null;
  }
  return found;
}

export async function approveStageOne(approvalId: string, actorUserId: string): Promise<ApprovalRecord | null> {
  const store = await readApprovalStore();
  const found = store.approvals[approvalId];
  if (!found) {
    return null;
  }
  if (found.status !== "pending_stage1") {
    return found;
  }
  const nextStatus: ApprovalStatus = "pending_stage2";
  const updated: ApprovalRecord = {
    ...found,
    status: nextStatus,
    history: [...found.history, { at: new Date().toISOString(), type: "stage1_approved", actorUserId }],
  };
  store.approvals[approvalId] = updated;
  await writeApprovalStore(store);
  return updated;
}

export async function rejectApprovalRequest(approvalId: string, actorUserId: string): Promise<ApprovalRecord | null> {
  const store = await readApprovalStore();
  const found = store.approvals[approvalId];
  if (!found) {
    return null;
  }
  if (found.status === "rejected") {
    return found;
  }
  const updated: ApprovalRecord = {
    ...found,
    status: "rejected",
    history: [...found.history, { at: new Date().toISOString(), type: "rejected", actorUserId }],
  };
  store.approvals[approvalId] = updated;
  await writeApprovalStore(store);
  return updated;
}

export async function markApprovalExecuted(approvalId: string, actorUserId: string): Promise<ApprovalRecord | null> {
  const store = await readApprovalStore();
  const found = store.approvals[approvalId];
  if (!found) {
    return null;
  }
  if (found.status === "executed") {
    return found;
  }
  const updated: ApprovalRecord = {
    ...found,
    status: "executed",
    history: [...found.history, { at: new Date().toISOString(), type: "executed", actorUserId }],
  };
  store.approvals[approvalId] = updated;
  await writeApprovalStore(store);
  return updated;
}

export async function listPendingApprovals(limit = 60): Promise<ApprovalRecord[]> {
  const store = await readApprovalStore();
  const pending = Object.values(store.approvals)
    .filter((item) => approvalIsPending(item.status))
    .sort((a, b) => (a.requestedAt < b.requestedAt ? 1 : -1));
  if (pending.length > Math.max(1, limit)) {
    return pending.slice(0, limit);
  }
  return pending;
}

export async function getApprovalQueueStats() {
  const store = await readApprovalStore();
  const approvals = Object.values(store.approvals);
  const stats = {
    pending: 0,
    pendingStage1: 0,
    pendingStage2: 0,
    executed: 0,
    rejected: 0,
    expired: 0,
    total: approvals.length,
    updatedAt: store.updatedAt,
  };

  for (const approval of approvals) {
    if (approval.status === "pending_stage1") {
      stats.pending += 1;
      stats.pendingStage1 += 1;
      continue;
    }
    if (approval.status === "pending_stage2") {
      stats.pending += 1;
      stats.pendingStage2 += 1;
      continue;
    }
    if (approval.status === "executed") {
      stats.executed += 1;
      continue;
    }
    if (approval.status === "rejected") {
      stats.rejected += 1;
      continue;
    }
    if (approval.status === "expired") {
      stats.expired += 1;
      continue;
    }
  }

  return stats;
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
  const history = await readJsonFile<TelegramChatHistoryStore>(TELEGRAM_CHAT_HISTORY_FILE, {});
  const current = Array.isArray(history[params.chatId]) ? history[params.chatId] : [];
  const next = [
    ...current,
    { role: "user" as const, text: params.userText, at: now },
    { role: "assistant" as const, text: params.assistantText, at: now },
  ].slice(-maxEntries);
  history[params.chatId] = next;
  await writeJsonFile(TELEGRAM_CHAT_HISTORY_FILE, history);
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
  const history = await readJsonFile<TelegramChatHistoryStore>(TELEGRAM_CHAT_HISTORY_FILE, {});
  if (history[chatId]) {
    delete history[chatId];
    await writeJsonFile(TELEGRAM_CHAT_HISTORY_FILE, history);
  }
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
