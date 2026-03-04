import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import type { CanonicalAgentId } from "@/lib/agents";
import type { AgentEvent } from "@/lib/orchestration/types";

type CompactSourceRef = { title: string; url: string };

export type CompactMemoryEntry = {
  id: string;
  at: string;
  source: "event" | "telegram";
  agentId: CanonicalAgentId;
  topicKey: string;
  summary: string;
  tags: string[];
  refs: CompactSourceRef[];
  scoreHint: number;
};

const ROOT = process.env.SHARED_ROOT_PATH?.trim() || path.join(process.cwd(), "shared_data");
const MEMORY_DIR = path.join(ROOT, "shared_memory");
const COMPACT_MEMORY_FILE = path.join(MEMORY_DIR, "compact_memory.json");
const COMPACT_MEMORY_MAX_ENTRIES = Math.max(60, Number(process.env.COMPACT_MEMORY_MAX_ENTRIES ?? 900) || 900);

const compact = (value: string | undefined, maxLen: number) => {
  const token = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!token) {
    return "";
  }
  if (token.length <= maxLen) {
    return token;
  }
  return `${token.slice(0, Math.max(12, maxLen - 1)).trimEnd()}…`;
};

const safeTags = (tags: string[] | undefined) =>
  (tags ?? [])
    .map((item) => item.trim().toLowerCase())
    .filter((item) => item.length > 0)
    .slice(0, 8);

const scoreHintFromPriority = (priority: AgentEvent["priority"]) => {
  if (priority === "critical") {
    return 1;
  }
  if (priority === "high") {
    return 0.8;
  }
  if (priority === "normal") {
    return 0.55;
  }
  return 0.35;
};

async function ensureMemoryDir() {
  await fs.mkdir(MEMORY_DIR, { recursive: true });
}

async function readEntries(): Promise<CompactMemoryEntry[]> {
  try {
    const raw = await fs.readFile(COMPACT_MEMORY_FILE, "utf-8");
    const data = JSON.parse(raw);
    return Array.isArray(data) ? (data as CompactMemoryEntry[]) : [];
  } catch {
    return [];
  }
}

async function writeEntries(entries: CompactMemoryEntry[]) {
  await ensureMemoryDir();
  const payload = JSON.stringify(entries.slice(-COMPACT_MEMORY_MAX_ENTRIES), null, 2);
  const tmp = `${COMPACT_MEMORY_FILE}.tmp`;
  await fs.writeFile(tmp, payload, "utf-8");
  await fs.rename(tmp, COMPACT_MEMORY_FILE);
}

export async function appendCompactMemoryFromEvent(event: AgentEvent) {
  const entry: CompactMemoryEntry = {
    id: event.eventId,
    at: event.createdAt,
    source: "event",
    agentId: event.agentId,
    topicKey: compact(event.topicKey, 80) || "event",
    summary: compact(event.summary, 240) || compact(event.title, 160) || "event",
    tags: safeTags(event.tags),
    refs: (event.sourceRefs ?? []).slice(0, 3).map((item) => ({
      title: compact(item.title, 80),
      url: compact(item.url, 180),
    })),
    scoreHint: scoreHintFromPriority(event.priority),
  };

  const entries = await readEntries();
  entries.push(entry);
  await writeEntries(entries);
}

export async function appendCompactMemoryFromTelegram(params: {
  chatId: string;
  at: string;
  userText: string;
  assistantText: string;
}) {
  const entry: CompactMemoryEntry = {
    id: crypto.randomUUID(),
    at: params.at,
    source: "telegram",
    agentId: "minerva",
    topicKey: `telegram-chat:${compact(params.chatId, 36)}`,
    summary: compact(`Q: ${params.userText} | A: ${params.assistantText}`, 260),
    tags: ["telegram", "conversation"],
    refs: [],
    scoreHint: 0.65,
  };
  const entries = await readEntries();
  entries.push(entry);
  await writeEntries(entries);
}

function tokenize(value: string): Set<string> {
  return new Set(
    value
      .toLowerCase()
      .split(/[^a-z0-9가-힣]+/g)
      .map((token) => token.trim())
      .filter((token) => token.length >= 2)
  );
}

function overlapScore(query: Set<string>, target: Set<string>) {
  if (query.size === 0 || target.size === 0) {
    return 0;
  }
  let hit = 0;
  for (const token of query) {
    if (target.has(token)) {
      hit += 1;
    }
  }
  return hit / Math.max(query.size, 1);
}

export async function buildCompactMemoryContext(params: {
  agentId: CanonicalAgentId;
  query: string;
  maxItems: number;
  maxChars: number;
}) {
  const entries = await readEntries();
  if (entries.length === 0) {
    return null;
  }

  const qTokens = tokenize(params.query);
  const now = Date.now();
  const candidates = entries
    .filter((item) => item.agentId === params.agentId || (params.agentId === "minerva" && item.agentId === "hermes"))
    .map((item) => {
      const ageHours = Math.max(0, (now - new Date(item.at).getTime()) / 1000 / 3600);
      const freshness = Math.exp(-ageHours / 72); // 3-day half-like decay
      const targetTokens = tokenize(`${item.topicKey} ${item.summary} ${item.tags.join(" ")}`);
      const relevance = overlapScore(qTokens, targetTokens);
      const score = relevance * 0.62 + freshness * 0.23 + Math.min(1, item.scoreHint) * 0.15;
      return { item, score };
    })
    .sort((a, b) => b.score - a.score);

  const picked = candidates.slice(0, Math.max(1, params.maxItems)).map((row) => row.item);
  if (picked.length === 0) {
    return null;
  }

  const lines: string[] = [];
  for (const item of picked) {
    lines.push(
      `- [${item.at}] (${item.agentId}) ${compact(item.summary, 160)}`
    );
    if (item.refs.length > 0) {
      for (const ref of item.refs.slice(0, 2)) {
        lines.push(`  source: ${compact(ref.title, 64)} | ${compact(ref.url, 120)}`);
      }
    }
  }
  const raw = lines.join("\n");
  if (raw.length <= params.maxChars) {
    return raw;
  }
  return `${raw.slice(0, Math.max(120, params.maxChars - 1)).trimEnd()}…`;
}
