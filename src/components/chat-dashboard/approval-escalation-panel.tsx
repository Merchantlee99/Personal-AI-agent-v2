"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./approval-escalation-panel.module.css";

type FrontendApprovalCard = {
  approvalId: string;
  action: "clio_save" | "hermes_deep_dive" | "minerva_insight";
  actionLabel: string;
  status: "pending_step1" | "pending_step2" | "approved" | "rejected" | "expired";
  eventId: string;
  title: string;
  summary: string;
  remainingSec: number;
  elapsedRatio: number;
};

type AgentUpdatesResponse = {
  ok: boolean;
  approvals?: FrontendApprovalCard[];
};

type DecisionResponse = {
  ok: boolean;
  status?: string;
  requireConfirmation?: boolean;
  error?: string;
};

function formatRemaining(value: number) {
  const safe = Math.max(0, Math.floor(value));
  const min = Math.floor(safe / 60);
  const sec = safe % 60;
  return `${min}:${String(sec).padStart(2, "0")}`;
}

function phaseLabel(status: FrontendApprovalCard["status"]) {
  if (status === "pending_step1") {
    return "1차 승인";
  }
  if (status === "pending_step2") {
    return "2차 확인";
  }
  return status;
}

export function ApprovalEscalationPanel() {
  const [approvals, setApprovals] = useState<FrontendApprovalCard[]>([]);
  const [requestingId, setRequestingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadApprovals = useCallback(async () => {
    try {
      const response = await fetch("/api/agent-updates", { cache: "no-store" });
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || "approval_updates_failed");
      }
      const payload = (await response.json()) as AgentUpdatesResponse;
      setApprovals(Array.isArray(payload.approvals) ? payload.approvals : []);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "approval_updates_failed");
    }
  }, []);

  useEffect(() => {
    void loadApprovals();
    const timer = window.setInterval(() => {
      void loadApprovals();
    }, 15000);
    return () => window.clearInterval(timer);
  }, [loadApprovals]);

  const hasCards = approvals.length > 0;
  const ordered = useMemo(
    () =>
      [...approvals].sort((a, b) => {
        if (a.status !== b.status) {
          return a.status === "pending_step2" ? -1 : 1;
        }
        return a.remainingSec - b.remainingSec;
      }),
    [approvals]
  );

  const submitDecision = useCallback(
    async (approvalId: string, decision: "yes" | "no") => {
      setRequestingId(approvalId);
      try {
        const response = await fetch("/api/agent-updates", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ approvalId, decision }),
        });
        const payload = (await response.json()) as DecisionResponse;
        if (!response.ok || payload.ok !== true) {
          throw new Error(payload.error || "approval_decision_failed");
        }
        await loadApprovals();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "approval_decision_failed");
      } finally {
        setRequestingId(null);
      }
    },
    [loadApprovals]
  );

  if (!hasCards && !error) {
    return null;
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.header}>⚠️ 승인 필요 알림</div>
      {error ? <div className={styles.error}>{error}</div> : null}
      {ordered.map((item) => {
        const pendingLabel = item.status === "pending_step2" ? "실수 방지 재확인 단계입니다." : "1차 승인 단계입니다.";
        const busy = requestingId === item.approvalId;
        return (
          <div key={item.approvalId} className={styles.card}>
            <div className={styles.title}>{item.actionLabel}</div>
            <div className={styles.meta}>
              <span>{phaseLabel(item.status)}</span>
              <span>남은 시간 {formatRemaining(item.remainingSec)}</span>
            </div>
            <div className={styles.topic}>{item.title}</div>
            <div className={styles.summary}>{item.summary}</div>
            <div className={styles.hint}>{pendingLabel}</div>
            <div className={styles.actions}>
              <button
                type="button"
                className={styles.yes}
                disabled={busy}
                onClick={() => void submitDecision(item.approvalId, "yes")}
              >
                네
              </button>
              <button
                type="button"
                className={styles.no}
                disabled={busy}
                onClick={() => void submitDecision(item.approvalId, "no")}
              >
                아니요
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
