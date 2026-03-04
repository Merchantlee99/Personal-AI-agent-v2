import { CanonicalAgentId } from "@/lib/agents";
import {
  canAgentUseCapability,
  findCapabilityDefinition,
  type CapabilityId,
} from "@/lib/orchestration/capability-registry";
import { createInboxTask } from "@/lib/orchestration/storage";
import { buildTelegramDispatchPayload, sendTelegramMessage } from "@/lib/orchestration/telegram";
import { AgentEvent, MinervaCalendarBriefing } from "@/lib/orchestration/types";

type CapabilityExecutionContext = {
  chatId?: string;
  event?: AgentEvent;
  calendarBriefing?: MinervaCalendarBriefing | null;
};

type CapabilityExecutionRequest = {
  capabilityId: CapabilityId;
  requestedBy: CanonicalAgentId;
  topicKey: string;
  title: string;
  summary: string;
  sourceRefs: Array<{ title: string; url: string }>;
  context?: CapabilityExecutionContext;
};

type CapabilityExecutionResult = {
  capabilityId: CapabilityId;
  adapterId: string;
  targetAgentId: CanonicalAgentId;
  inbox?: {
    inboxFile: string;
    path: string;
  };
  delivery?: {
    channel: "telegram";
    sent: boolean;
    reason: string;
    status?: number;
    detail?: string;
  };
};

type AdapterHandler = (params: {
  capabilityId: CapabilityId;
  targetAgentId: CanonicalAgentId;
  reason: string;
  topicKey: string;
  title: string;
  summary: string;
  sourceRefs: Array<{ title: string; url: string }>;
  context?: CapabilityExecutionContext;
}) => Promise<CapabilityExecutionResult>;

const ADAPTERS = new Map<string, AdapterHandler>();

function registerAdapter(adapterId: string, handler: AdapterHandler) {
  ADAPTERS.set(adapterId, handler);
}

registerAdapter("inbox_task_v1", async (params) => {
  const inbox = await createInboxTask({
    targetAgentId: params.targetAgentId,
    reason: params.reason,
    topicKey: params.topicKey,
    title: params.title,
    summary: params.summary,
    sourceRefs: params.sourceRefs,
  });
  return {
    capabilityId: params.capabilityId,
    adapterId: "inbox_task_v1",
    targetAgentId: params.targetAgentId,
    inbox,
  };
});

registerAdapter("channel_telegram_v1", async (params) => {
  const chatId = params.context?.chatId?.trim() ?? "";
  const event = params.context?.event;
  if (!chatId || !event) {
    throw new Error("channel_telegram_v1_missing_context");
  }

  const payload = await buildTelegramDispatchPayload({
    chatId,
    event,
    calendarBriefing: params.context?.calendarBriefing ?? null,
  });
  const sendResult = await sendTelegramMessage(payload);

  return {
    capabilityId: params.capabilityId,
    adapterId: "channel_telegram_v1",
    targetAgentId: params.targetAgentId,
    delivery: {
      channel: "telegram",
      sent: sendResult.sent,
      reason: sendResult.reason,
      status: sendResult.status,
      detail: sendResult.detail,
    },
  };
});

export async function executeCapability(params: CapabilityExecutionRequest): Promise<CapabilityExecutionResult> {
  const capability = findCapabilityDefinition(params.capabilityId);
  if (!capability) {
    throw new Error(`unknown_capability:${params.capabilityId}`);
  }
  if (!canAgentUseCapability(params.requestedBy, params.capabilityId)) {
    throw new Error(`capability_not_allowed:${params.requestedBy}:${params.capabilityId}`);
  }

  const adapter = ADAPTERS.get(capability.adapter);
  if (!adapter) {
    throw new Error(`adapter_not_found:${capability.adapter}`);
  }

  return adapter({
    capabilityId: params.capabilityId,
    targetAgentId: capability.target_agent_id,
    reason: capability.reason,
    topicKey: params.topicKey,
    title: params.title,
    summary: params.summary,
    sourceRefs: params.sourceRefs,
    context: params.context,
  });
}
