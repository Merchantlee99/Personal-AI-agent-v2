"use client";

import { DashboardView } from "./chat-dashboard/dashboard-view";
import { useChatController } from "./chat-dashboard/use-chat-controller";

export function FullChatDashboard() {
  const controller = useChatController();
  return <DashboardView controller={controller} />;
}
