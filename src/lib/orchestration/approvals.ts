import { executeCapability } from "@/lib/orchestration/capability-adapters";
import type { CapabilityId } from "@/lib/orchestration/capability-registry";
import { findEventById } from "@/lib/orchestration/storage";

export type InlineAction = "clio_save" | "hermes_deep_dive" | "minerva_insight";

export function isInlineAction(action: string): action is InlineAction {
  return action === "clio_save" || action === "hermes_deep_dive" || action === "minerva_insight";
}

export function inlineActionLabel(action: InlineAction): string {
  if (action === "clio_save") {
    return "Clio, 옵시디언에 저장해";
  }
  if (action === "hermes_deep_dive") {
    return "Hermes, 더 찾아";
  }
  return "Minerva, 인사이트 분석해";
}

function capabilityFromInlineAction(action: InlineAction): CapabilityId {
  if (action === "clio_save") {
    return "knowledge.store_obsidian";
  }
  if (action === "hermes_deep_dive") {
    return "research.deep_dive";
  }
  return "analysis.minerva_insight";
}

function buildInlineSummary(action: InlineAction, eventSummary: string): string {
  if (action === "clio_save") {
    return (
      `다음 내용을 Clio Obsidian 저장 포맷으로 정리해 저장하세요.\n` +
      `- 핵심 요약: ${eventSummary}\n` +
      `- 필수 출력: 태그, 관련 노트 링크, 출처 URL, notebooklm_ready 메타`
    );
  }
  if (action === "hermes_deep_dive") {
    return (
      `다음 주제와 직접 관련된 뉴스/아티클/트렌드 신호를 더 찾아주세요.\n` +
      `- 기준 요약: ${eventSummary}\n` +
      `- 역할 제한: 사실/근거 수집만 수행하고, 최종 판단·전략 결론은 작성하지 마세요.\n` +
      `- 요청 출력: 관련 출처 5개 이상, 상충 관점 1개 이상, 핵심 변화 요약(데이터 중심)\n` +
      `- 후속 처리: 처리 완료 후 Minerva 인사이트 분석 태스크가 자동 생성됩니다.`
    );
  }
  return (
    `다음 주제에 대해 Minerva 2차적 사고 기반 인사이트 분석을 수행하세요.\n` +
    `- 핵심 변화(1차): ${eventSummary}\n` +
    `- 2차 분석: 원인-결과 연결고리, 파급 영향도, 리스크/기회 분해\n` +
    `- 요청 출력: 우선순위 액션 3개`
  );
}

function callbackText(action: InlineAction): string {
  if (action === "clio_save") {
    return "Clio 옵시디언 저장 요청을 접수했습니다.";
  }
  if (action === "hermes_deep_dive") {
    return "Hermes 근거 수집 요청을 접수했습니다. 완료 후 Minerva 분석이 자동 연결됩니다.";
  }
  return "Minerva 2차 인사이트 분석 요청을 접수했습니다.";
}

export async function createInboxFromInlineAction(params: { action: InlineAction; eventId: string }) {
  const event = await findEventById(params.eventId);
  if (!event) {
    return { ok: false as const, reason: "event_not_found" };
  }

  let execution;
  try {
    execution = await executeCapability({
      capabilityId: capabilityFromInlineAction(params.action),
      requestedBy: "minerva",
      topicKey: event.topicKey,
      title: event.title,
      summary: buildInlineSummary(params.action, event.summary),
      sourceRefs: (event.sourceRefs ?? []).map((item) => ({ title: item.title, url: item.url })),
    });
  } catch {
    return { ok: false as const, reason: "capability_execution_failed" };
  }
  if (!execution.inbox) {
    return { ok: false as const, reason: "capability_execution_failed" };
  }

  return {
    ok: true as const,
    event,
    inbox: execution.inbox,
    callbackText: callbackText(params.action),
  };
}
