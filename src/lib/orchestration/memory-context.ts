import fs from "node:fs/promises";
import path from "node:path";
import type { CanonicalAgentId } from "@/lib/agents";
import { buildCompactMemoryContext } from "@/lib/orchestration/compact-memory";
import { getAgentMemoryMarkdownPath } from "@/lib/orchestration/storage";

const ROOT = process.env.SHARED_ROOT_PATH?.trim() || path.join(process.cwd(), "shared_data");
const FALLBACK_MEMORY_DIR = path.join(ROOT, "shared_memory", "agent_memory");

function compactText(raw: string) {
  return raw
    .replace(/\r/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function truncateFromEnd(raw: string, maxChars: number) {
  if (raw.length <= maxChars) {
    return raw;
  }
  return `…\n${raw.slice(raw.length - maxChars).trimStart()}`;
}

function pickRecentBlocks(raw: string, maxBlocks: number) {
  const normalized = compactText(raw);
  if (!normalized) {
    return "";
  }
  const parts = normalized.split(/\n(?=###\s)/g);
  const timelineBlocks = parts.filter((item) => item.trim().startsWith("### "));
  if (timelineBlocks.length === 0) {
    return normalized;
  }
  return timelineBlocks.slice(-Math.max(1, maxBlocks)).join("\n\n");
}

async function readMemoryFile(filePath: string) {
  try {
    return await fs.readFile(filePath, "utf-8");
  } catch {
    return "";
  }
}

export async function readAgentMemoryContext(
  agentId: CanonicalAgentId,
  opts?: { maxChars?: number; maxBlocks?: number; maxItems?: number; query?: string }
): Promise<string | null> {
  const maxChars = Math.max(200, opts?.maxChars ?? 900);
  const maxBlocks = Math.max(1, opts?.maxBlocks ?? 4);
  const maxItems = Math.max(1, opts?.maxItems ?? 4);
  const query = String(opts?.query ?? "").trim();

  const compactContext = await buildCompactMemoryContext({
    agentId,
    query,
    maxItems,
    maxChars,
  });
  if (compactContext) {
    return compactContext;
  }

  const primaryPath = getAgentMemoryMarkdownPath(agentId);
  const fallbackPath = path.join(FALLBACK_MEMORY_DIR, `${agentId}.md`);

  const raw = (await readMemoryFile(primaryPath)) || (await readMemoryFile(fallbackPath));
  if (!raw.trim()) {
    return null;
  }
  const recent = pickRecentBlocks(raw, maxBlocks);
  if (!recent.trim()) {
    return null;
  }
  return truncateFromEnd(recent, maxChars);
}
