import { AgentEvent, MinervaCalendarBriefing } from "@/lib/orchestration/types";
import { executeCapability } from "@/lib/orchestration/capability-adapters";

export type OutboundChannel = "telegram";

type DispatchParams = {
  chatId: string;
  event: AgentEvent;
  calendarBriefing?: MinervaCalendarBriefing | null;
  channel?: string;
};

type DispatchResult = {
  sent: boolean;
  reason: string;
  channel: OutboundChannel | "unknown";
};

const DEFAULT_CHANNEL: OutboundChannel = "telegram";

function resolveChannel(raw: string | undefined): OutboundChannel | "unknown" {
  const token = (raw ?? process.env.ORCHESTRATION_PRIMARY_CHANNEL ?? DEFAULT_CHANNEL).trim().toLowerCase();
  if (token === "telegram" || token.length === 0) {
    return "telegram";
  }
  return "unknown";
}

export async function dispatchBriefingToPrimaryChannel(params: DispatchParams): Promise<DispatchResult> {
  const channel = resolveChannel(params.channel);
  if (channel === "unknown") {
    return { sent: false, reason: "unsupported_channel", channel };
  }

  try {
    const execution = await executeCapability({
      capabilityId: "notify.telegram_briefing",
      requestedBy: "minerva",
      topicKey: params.event.topicKey,
      title: params.event.title,
      summary: params.event.summary,
      sourceRefs: (params.event.sourceRefs ?? []).map((item) => ({ title: item.title, url: item.url })),
      context: {
        chatId: params.chatId,
        event: params.event,
        calendarBriefing: params.calendarBriefing ?? null,
      },
    });
    const delivery = execution.delivery;
    if (delivery?.sent) {
      return { sent: true, reason: "ok", channel };
    }
    return { sent: false, reason: delivery?.reason ?? "channel_delivery_missing", channel };
  } catch {
    return { sent: false, reason: "channel_dispatch_failed", channel };
  }
}
