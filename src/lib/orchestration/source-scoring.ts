import { AgentEventInput, EventPriority, SourceRef } from "@/lib/orchestration/types";

type ScoreBreakdown = {
  sourceReliability: number;
  impact: number;
  userRelevance: number;
  novelty: number;
  corroboration: number;
  recency: number;
  dedupPenalty: number;
};

export type SignalScoreResult = {
  alertScore: number;
  computedConfidence: number;
  breakdown: ScoreBreakdown;
};

const DEFAULT_WEIGHTS = {
  sourceReliability: 0.3,
  impact: 0.25,
  userRelevance: 0.2,
  novelty: 0.1,
  corroboration: 0.1,
  recency: 0.05,
};

const PRIORITY_IMPACT: Record<EventPriority, number> = {
  critical: 92,
  high: 82,
  normal: 66,
  low: 48,
};

function clampScore(value: number) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(100, value));
}

function normalizeDomain(url: string) {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return "";
  }
}

function reliabilityFromSource(ref: SourceRef) {
  const category = (ref.category ?? "").toLowerCase();
  const domain = normalizeDomain(ref.url);

  if (
    domain.endsWith("openai.com") ||
    domain.endsWith("anthropic.com") ||
    domain.endsWith("google.com") ||
    domain.endsWith("deepmind.google") ||
    domain.endsWith("arxiv.org")
  ) {
    return 92;
  }
  if (
    category.includes("engineering") ||
    category.includes("super_app") ||
    category.includes("mobility") ||
    category.includes("ai_growth")
  ) {
    return 78;
  }
  if (category.includes("aggregator") || domain.includes("news") || domain.includes("techcrunch")) {
    return 62;
  }
  return 68;
}

function recencyScore(refs: SourceRef[]) {
  const now = Date.now();
  const scored = refs
    .map((ref) => new Date(ref.publishedAt ?? "").getTime())
    .filter((value) => Number.isFinite(value))
    .map((publishedAt) => {
      const ageHours = Math.max(0, (now - publishedAt) / (1000 * 60 * 60));
      if (ageHours <= 24) {
        return 90;
      }
      if (ageHours <= 72) {
        return 76;
      }
      if (ageHours <= 168) {
        return 62;
      }
      return 50;
    });
  if (scored.length === 0) {
    return 70;
  }
  return scored.reduce((sum, value) => sum + value, 0) / scored.length;
}

function normalizeTitle(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9가-힣]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function dedupStats(refs: SourceRef[]) {
  const titleSet = new Set<string>();
  const domainSet = new Set<string>();
  let duplicateHits = 0;

  for (const ref of refs) {
    const title = normalizeTitle(ref.title ?? "");
    const domain = normalizeDomain(ref.url);
    if (title && titleSet.has(title)) {
      duplicateHits += 1;
    }
    if (title) {
      titleSet.add(title);
    }
    if (domain) {
      if (domainSet.has(domain)) {
        duplicateHits += 0.5;
      }
      domainSet.add(domain);
    }
  }

  const novelty = refs.length === 0 ? 55 : clampScore(100 - duplicateHits * 18);
  const corroboration = refs.length === 0 ? 40 : clampScore(Math.min(100, 40 + domainSet.size * 18));
  const dedupPenalty = clampScore(Math.min(20, duplicateHits * 6));
  return { novelty, corroboration, dedupPenalty, uniqueDomainCount: domainSet.size };
}

function userRelevanceScore(refs: SourceRef[]) {
  if (refs.length === 0) {
    return 55;
  }
  const categories = refs.map((ref) => (ref.category ?? "").toLowerCase());
  const preferred = categories.filter(
    (category) =>
      category.includes("kr_super_app") ||
      category.includes("kr_engineering_core") ||
      category.includes("kr_mobility") ||
      category.includes("kr_ai_growth") ||
      category.includes("global_ai") ||
      category.includes("global_strategy")
  ).length;
  const ratio = preferred / categories.length;
  return clampScore(55 + ratio * 35);
}

function computeImpact(event: AgentEventInput) {
  const rawImpact = Number(event.impactScore ?? NaN);
  if (Number.isFinite(rawImpact)) {
    return clampScore(rawImpact * 100);
  }
  return PRIORITY_IMPACT[event.priority];
}

export function scoreEventSignal(event: AgentEventInput): SignalScoreResult {
  const refs = event.sourceRefs ?? [];
  const sourceReliability =
    refs.length === 0 ? 58 : refs.map((ref) => reliabilityFromSource(ref)).reduce((sum, value) => sum + value, 0) / refs.length;
  const impact = computeImpact(event);
  const userRelevance = userRelevanceScore(refs);
  const recency = recencyScore(refs);
  const dedup = dedupStats(refs);

  const weightedScore =
    sourceReliability * DEFAULT_WEIGHTS.sourceReliability +
    impact * DEFAULT_WEIGHTS.impact +
    userRelevance * DEFAULT_WEIGHTS.userRelevance +
    dedup.novelty * DEFAULT_WEIGHTS.novelty +
    dedup.corroboration * DEFAULT_WEIGHTS.corroboration +
    recency * DEFAULT_WEIGHTS.recency -
    dedup.dedupPenalty;

  const alertScore = clampScore(weightedScore);
  const computedConfidence = clampScore(sourceReliability * 0.45 + dedup.corroboration * 0.35 + recency * 0.2) / 100;

  return {
    alertScore,
    computedConfidence,
    breakdown: {
      sourceReliability: clampScore(sourceReliability),
      impact: clampScore(impact),
      userRelevance: clampScore(userRelevance),
      novelty: clampScore(dedup.novelty),
      corroboration: clampScore(dedup.corroboration),
      recency: clampScore(recency),
      dedupPenalty: clampScore(dedup.dedupPenalty),
    },
  };
}
