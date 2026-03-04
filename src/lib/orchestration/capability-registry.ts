import capabilityConfig from "../../../config/capabilities.json";
import { CanonicalAgentId } from "@/lib/agents";

export type CapabilityId =
  | "knowledge.store_obsidian"
  | "research.deep_dive"
  | "analysis.minerva_insight"
  | "notify.telegram_briefing";

export type CapabilityDefinition = {
  display_name: string;
  owners: CanonicalAgentId[];
  target_agent_id: CanonicalAgentId;
  adapter: string;
  reason: string;
};

type CapabilityRegistry = {
  version: string;
  capabilities: Record<string, CapabilityDefinition>;
};

const DEFAULT_REGISTRY: CapabilityRegistry = {
  version: "1.0",
  capabilities: {
    "knowledge.store_obsidian": {
      display_name: "Store to Clio Obsidian",
      owners: ["minerva", "clio"],
      target_agent_id: "clio",
      adapter: "inbox_task_v1",
      reason: "capability_knowledge_store_obsidian",
    },
    "research.deep_dive": {
      display_name: "Hermes Deep Dive",
      owners: ["minerva", "hermes"],
      target_agent_id: "hermes",
      adapter: "inbox_task_v1",
      reason: "capability_research_deep_dive",
    },
    "analysis.minerva_insight": {
      display_name: "Minerva Insight Analysis",
      owners: ["minerva"],
      target_agent_id: "minerva",
      adapter: "inbox_task_v1",
      reason: "capability_analysis_minerva_insight",
    },
    "notify.telegram_briefing": {
      display_name: "Telegram Briefing Dispatch",
      owners: ["minerva"],
      target_agent_id: "minerva",
      adapter: "channel_telegram_v1",
      reason: "capability_notify_telegram_briefing",
    },
  },
};

function isCanonicalAgentId(value: string): value is CanonicalAgentId {
  return value === "minerva" || value === "clio" || value === "hermes";
}

function parseRegistry(raw: unknown): CapabilityRegistry | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  const payload = raw as Record<string, unknown>;
  const version = String(payload.version ?? "").trim() || DEFAULT_REGISTRY.version;
  const capabilitiesRaw = payload.capabilities;
  if (!capabilitiesRaw || typeof capabilitiesRaw !== "object" || Array.isArray(capabilitiesRaw)) {
    return null;
  }

  const capabilities: Record<string, CapabilityDefinition> = {};
  for (const [id, rawEntry] of Object.entries(capabilitiesRaw as Record<string, unknown>)) {
    if (!rawEntry || typeof rawEntry !== "object" || Array.isArray(rawEntry)) {
      continue;
    }
    const entry = rawEntry as Record<string, unknown>;
    const displayName = String(entry.display_name ?? "").trim();
    const adapter = String(entry.adapter ?? "").trim();
    const reason = String(entry.reason ?? "").trim();
    const target = String(entry.target_agent_id ?? "").trim().toLowerCase();
    const owners = Array.isArray(entry.owners)
      ? entry.owners.map((item) => String(item).trim().toLowerCase()).filter(isCanonicalAgentId)
      : [];

    if (!displayName || !adapter || !reason || !isCanonicalAgentId(target) || owners.length === 0) {
      continue;
    }

    capabilities[id] = {
      display_name: displayName,
      owners,
      target_agent_id: target,
      adapter,
      reason,
    };
  }

  if (Object.keys(capabilities).length === 0) {
    return null;
  }
  return { version, capabilities };
}

const REGISTRY = parseRegistry(capabilityConfig) ?? DEFAULT_REGISTRY;

export function getCapabilityRegistry(): CapabilityRegistry {
  return REGISTRY;
}

export function findCapabilityDefinition(capabilityId: string): CapabilityDefinition | null {
  return REGISTRY.capabilities[capabilityId] ?? null;
}

export function canAgentUseCapability(agentId: CanonicalAgentId, capabilityId: string): boolean {
  const capability = findCapabilityDefinition(capabilityId);
  if (!capability) {
    return false;
  }
  return capability.owners.includes(agentId);
}

