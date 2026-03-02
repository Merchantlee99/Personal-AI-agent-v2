import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { AgentEvent } from "@/lib/orchestration/types";

type CooldownState = Record<string, string>;

type DigestBucket = {
  slot: string;
  items: AgentEvent[];
  updatedAt: string;
};

const ROOT = process.env.SHARED_ROOT_PATH?.trim() || path.join(process.cwd(), "shared_data");
const MEMORY_DIR = path.join(ROOT, "shared_memory");
const EVENTS_FILE = path.join(MEMORY_DIR, "agent_events.json");
const COOLDOWN_FILE = path.join(MEMORY_DIR, "topic_cooldowns.json");
const DIGEST_FILE = path.join(MEMORY_DIR, "digest_queue.json");

async function ensureMemoryDir() {
  await fs.mkdir(MEMORY_DIR, { recursive: true });
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
  targetAgentId: "clio" | "hermes";
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

