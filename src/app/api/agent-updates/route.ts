import { NextRequest, NextResponse } from "next/server";
import { createInboxFromInlineAction, isInlineAction, inlineActionLabel } from "@/lib/orchestration/approvals";
import { resolveApprovalEscalationRatio } from "@/lib/orchestration/policy";
import {
  findEventById,
  findTelegramPendingApproval,
  listTelegramPendingApprovals,
  updateTelegramPendingApproval,
} from "@/lib/orchestration/storage";

type ApprovalDecision = "yes" | "no";

type ApprovalDecisionRequest = {
  approvalId?: string;
  decision?: ApprovalDecision;
};

function firstCsvValue(raw: string | undefined) {
  if (!raw) {
    return "";
  }
  const value = raw
    .split(",")
    .map((item) => item.trim())
    .find((item) => item.length > 0);
  return value ?? "";
}

function resolveFrontendChatId(request: NextRequest) {
  const fromQuery = (request.nextUrl.searchParams.get("chatId") ?? "").trim();
  if (fromQuery) {
    return fromQuery;
  }
  const fromAllowlist = firstCsvValue(process.env.TELEGRAM_ALLOWED_CHAT_IDS);
  if (fromAllowlist) {
    return fromAllowlist;
  }
  return (process.env.TELEGRAM_CHAT_ID ?? "").trim();
}

function computeApprovalProgress(params: { createdAt: string; expiresAt: string }) {
  const now = Date.now();
  const createdAt = new Date(params.createdAt).getTime();
  const expiresAt = new Date(params.expiresAt).getTime();
  if (!Number.isFinite(createdAt) || !Number.isFinite(expiresAt) || expiresAt <= createdAt) {
    return {
      totalSec: 0,
      elapsedSec: 0,
      remainingSec: 0,
      elapsedRatio: 1,
    };
  }
  const totalSec = Math.max(1, Math.floor((expiresAt - createdAt) / 1000));
  const elapsedSec = Math.max(0, Math.floor((now - createdAt) / 1000));
  const remainingSec = Math.max(0, Math.floor((expiresAt - now) / 1000));
  const elapsedRatio = Math.min(1, Math.max(0, elapsedSec / totalSec));
  return { totalSec, elapsedSec, remainingSec, elapsedRatio };
}

function parseDecisionBody(payload: unknown): ApprovalDecisionRequest {
  if (!payload || typeof payload !== "object") {
    return {};
  }
  const data = payload as Record<string, unknown>;
  const approvalId = typeof data.approvalId === "string" ? data.approvalId.trim() : "";
  const decisionRaw = typeof data.decision === "string" ? data.decision.trim().toLowerCase() : "";
  const decision = decisionRaw === "yes" || decisionRaw === "no" ? decisionRaw : undefined;
  return { approvalId, decision };
}

export async function GET(request: NextRequest) {
  const chatId = resolveFrontendChatId(request);
  if (!chatId) {
    return NextResponse.json({ ok: false, error: "chat_id_not_configured" }, { status: 400 });
  }

  const escalationRatio = resolveApprovalEscalationRatio(request.nextUrl.searchParams.get("escalationRatio"));
  const approvals = await listTelegramPendingApprovals({
    chatId,
    statuses: ["pending_step1", "pending_step2"],
  });

  const cards = await Promise.all(
    approvals.map(async (approval) => {
      const event = await findEventById(approval.eventId);
      const progress = computeApprovalProgress({
        createdAt: approval.createdAt,
        expiresAt: approval.expiresAt,
      });
      const escalatedToFrontend = progress.elapsedRatio >= escalationRatio && progress.remainingSec > 0;
      return {
        approvalId: approval.approvalId,
        action: approval.action,
        actionLabel: inlineActionLabel(approval.action),
        status: approval.status,
        eventId: approval.eventId,
        title: event?.title ?? "원본 이벤트를 찾을 수 없음",
        summary: event?.summary ?? "원본 이벤트를 찾을 수 없어 상세 내용을 표시할 수 없습니다.",
        createdAt: approval.createdAt,
        expiresAt: approval.expiresAt,
        totalSec: progress.totalSec,
        elapsedSec: progress.elapsedSec,
        remainingSec: progress.remainingSec,
        elapsedRatio: progress.elapsedRatio,
        escalatedToFrontend,
      };
    })
  );

  return NextResponse.json({
    ok: true,
    now: new Date().toISOString(),
    chatId,
    escalationRatio,
    approvals: cards.filter((item) => item.escalatedToFrontend),
  });
}

export async function POST(request: NextRequest) {
  const body = parseDecisionBody(await request.json());
  if (!body.approvalId || !body.decision) {
    return NextResponse.json({ ok: false, error: "invalid_request", required: ["approvalId", "decision"] }, { status: 400 });
  }

  const approval = await findTelegramPendingApproval(body.approvalId);
  if (!approval) {
    return NextResponse.json({ ok: false, error: "approval_not_found" }, { status: 404 });
  }

  if (approval.status === "expired") {
    return NextResponse.json({ ok: false, error: "approval_expired" }, { status: 409 });
  }
  if (approval.status === "rejected") {
    return NextResponse.json({ ok: true, alreadyResolved: true, status: approval.status });
  }
  if (approval.status === "approved") {
    return NextResponse.json({ ok: true, alreadyResolved: true, status: approval.status });
  }

  if (body.decision === "no") {
    const rejected = await updateTelegramPendingApproval({
      approvalId: approval.approvalId,
      status: "rejected",
      resolvedReason: approval.status === "pending_step1" ? "frontend_rejected_step1" : "frontend_rejected_step2",
    });
    return NextResponse.json({
      ok: true,
      action: approval.action,
      status: rejected?.status ?? "rejected",
      phase: approval.status,
      cancelled: true,
    });
  }

  if (approval.status === "pending_step1") {
    const updated = await updateTelegramPendingApproval({
      approvalId: approval.approvalId,
      status: "pending_step2",
    });
    return NextResponse.json({
      ok: true,
      action: approval.action,
      status: updated?.status ?? "pending_step2",
      phase: "pending_step2",
      requireConfirmation: true,
    });
  }

  if (!isInlineAction(approval.action)) {
    await updateTelegramPendingApproval({
      approvalId: approval.approvalId,
      status: "expired",
      resolvedReason: "unsupported_action",
    });
    return NextResponse.json({ ok: false, error: "unsupported_action" }, { status: 400 });
  }

  const execution = await createInboxFromInlineAction({ action: approval.action, eventId: approval.eventId });
  if (!execution.ok) {
    const resolvedReason = execution.reason === "event_not_found" ? "event_not_found" : "capability_execution_failed";
    await updateTelegramPendingApproval({
      approvalId: approval.approvalId,
      status: "expired",
      resolvedReason,
    });
    if (execution.reason === "event_not_found") {
      return NextResponse.json({ ok: false, error: "event_not_found" }, { status: 404 });
    }
    return NextResponse.json({ ok: false, error: "capability_execution_failed" }, { status: 500 });
  }

  const updated = await updateTelegramPendingApproval({
    approvalId: approval.approvalId,
    status: "approved",
    resolvedReason: "frontend_approved_step2",
  });
  return NextResponse.json({
    ok: true,
    action: approval.action,
    status: updated?.status ?? "approved",
    phase: "completed",
    inbox: execution.inbox,
    callbackText: execution.callbackText,
  });
}
