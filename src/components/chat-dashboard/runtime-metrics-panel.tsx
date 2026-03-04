"use client";

import { useEffect, useState } from "react";
import styles from "./runtime-metrics-panel.module.css";

type RuntimeMetrics = {
  ok: boolean;
  day: string;
  updatedAt: string | null;
  llm: {
    total: number;
    success: number;
    transientError: number;
    fatalError: number;
    quota429: number;
    fallbackApplied: number;
    successRate: number;
    latencyMs: {
      avg: number;
      p95: number;
      max: number;
      samples: number;
    };
  };
  orchestration: {
    todayEvents: number;
    pendingApprovals: number;
    autoClioCreated: number;
    byDecision: {
      send_now: number;
      queue_digest: number;
      suppressed_cooldown: number;
    };
    telegram: {
      attempted: number;
      sent: number;
      failed: number;
      successRate: number;
    };
    approvals: {
      pending_step1: number;
      pending_step2: number;
      approved: number;
      rejected: number;
      expired: number;
    };
  };
  deepl: {
    attempts: number;
    translated: number;
    cached: number;
    skipped: number;
    failed: number;
    inputChars: number;
    translatedChars: number;
    successRate: number;
  };
};

export function RuntimeMetricsPanel() {
  const [metrics, setMetrics] = useState<RuntimeMetrics | null>(null);

  useEffect(() => {
    let mounted = true;

    const load = async () => {
      try {
        const response = await fetch("/api/runtime-metrics", { cache: "no-store" });
        if (!response.ok) {
          return;
        }
        const payload = (await response.json()) as RuntimeMetrics;
        if (mounted) {
          setMetrics(payload);
        }
      } catch {
        // non-blocking metrics panel
      }
    };

    void load();
    const timer = window.setInterval(() => {
      void load();
    }, 20000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, []);

  if (!metrics) {
    return null;
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.title}>운영 지표</div>
      <div className={styles.grid}>
        <div className={styles.item}>
          <span>LLM 성공률</span>
          <strong>{metrics.llm.successRate}%</strong>
        </div>
        <div className={styles.item}>
          <span>LLM 지연 p95</span>
          <strong>{metrics.llm.latencyMs.p95}ms</strong>
        </div>
        <div className={styles.item}>
          <span>Quota 429</span>
          <strong>{metrics.llm.quota429}</strong>
        </div>
        <div className={styles.item}>
          <span>Fallback</span>
          <strong>{metrics.llm.fallbackApplied}</strong>
        </div>
        <div className={styles.item}>
          <span>오늘 이벤트</span>
          <strong>{metrics.orchestration.todayEvents}</strong>
        </div>
        <div className={styles.item}>
          <span>즉시 전송</span>
          <strong>{metrics.orchestration.byDecision.send_now}</strong>
        </div>
        <div className={styles.item}>
          <span>텔레그램 성공률</span>
          <strong>{metrics.orchestration.telegram.successRate}%</strong>
        </div>
        <div className={styles.item}>
          <span>자동 Clio 저장</span>
          <strong>{metrics.orchestration.autoClioCreated}</strong>
        </div>
        <div className={styles.item}>
          <span>대기 승인</span>
          <strong>{metrics.orchestration.pendingApprovals}</strong>
        </div>
        <div className={styles.item}>
          <span>승인 완료</span>
          <strong>{metrics.orchestration.approvals.approved}</strong>
        </div>
        <div className={styles.item}>
          <span>DeepL 성공률</span>
          <strong>{metrics.deepl.successRate}%</strong>
        </div>
        <div className={styles.item}>
          <span>오늘 번역</span>
          <strong>{metrics.deepl.translated}</strong>
        </div>
      </div>
    </div>
  );
}
