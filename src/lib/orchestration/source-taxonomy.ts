import { SourceRef } from "@/lib/orchestration/types";

export type SourceTier = "P0" | "P1" | "P2";

type SourceRule = {
  category: string;
  tier: SourceTier;
  publisher: string;
  hosts: string[];
};

const SOURCE_RULES: SourceRule[] = [
  // P0: 반드시 챙길 소스 (신뢰도/속도/임팩트)
  { category: "kr_super_app", tier: "P0", publisher: "Toss Tech", hosts: ["toss.tech"] },
  { category: "kr_engineering_core", tier: "P0", publisher: "Naver D2", hosts: ["d2.naver.com"] },
  { category: "kr_engineering_core", tier: "P0", publisher: "Kakao Tech", hosts: ["tech.kakao.com"] },
  { category: "kr_aggregator", tier: "P0", publisher: "GeekNews", hosts: ["news.hada.io"] },
  { category: "global_ai", tier: "P0", publisher: "OpenAI News", hosts: ["openai.com"] },
  { category: "global_ai", tier: "P0", publisher: "Anthropic News", hosts: ["anthropic.com"] },
  { category: "global_bigtech", tier: "P0", publisher: "Cloudflare Blog", hosts: ["blog.cloudflare.com"] },
  { category: "global_bigtech", tier: "P0", publisher: "AWS News Blog", hosts: ["aws.amazon.com"] },
  { category: "global_aggregator", tier: "P0", publisher: "Hacker News", hosts: ["news.ycombinator.com"] },

  // P1: 강력 추천 소스 (주 3~7회 동향 반영)
  { category: "kr_super_app", tier: "P1", publisher: "Woowahan Tech", hosts: ["techblog.woowahan.com"] },
  { category: "kr_super_app", tier: "P1", publisher: "Karrot Tech", hosts: ["daangn.com", "karrot.com"] },
  { category: "kr_mobility", tier: "P1", publisher: "MyRealTrip Tech", hosts: ["blog.myrealtrip.com"] },
  { category: "kr_mobility", tier: "P1", publisher: "Socar Tech", hosts: ["tech.socarcorp.kr"] },
  { category: "kr_ai_growth", tier: "P1", publisher: "Upstage", hosts: ["upstage.ai"] },
  { category: "kr_ai_growth", tier: "P1", publisher: "AB180", hosts: ["blog.ab180.co"] },
  { category: "global_bigtech", tier: "P1", publisher: "Netflix TechBlog", hosts: ["netflixtechblog.com"] },
  { category: "global_bigtech", tier: "P1", publisher: "Airbnb Engineering", hosts: ["airbnb.tech"] },
  { category: "global_bigtech", tier: "P1", publisher: "Uber Engineering", hosts: ["uber.com"] },
  { category: "global_ai", tier: "P1", publisher: "Hugging Face Blog", hosts: ["huggingface.co"] },
  { category: "global_ai", tier: "P1", publisher: "Google DeepMind Blog", hosts: ["deepmind.google"] },
  { category: "global_strategy", tier: "P1", publisher: "Stratechery", hosts: ["stratechery.com"] },
  { category: "global_strategy", tier: "P1", publisher: "Lenny's Newsletter", hosts: ["lennysnewsletter.com"] },

  // P2: 보강 소스 (주 1~3회 스캔)
  { category: "kr_super_app", tier: "P2", publisher: "LINE Engineering", hosts: ["engineering.linecorp.com"] },
  { category: "kr_mobility", tier: "P2", publisher: "Yanolja Tech", hosts: ["yanolja.github.io"] },
  { category: "kr_mobility", tier: "P2", publisher: "Tmap Mobility Tech", hosts: ["tmapmobility.com"] },
  { category: "kr_ai_growth", tier: "P2", publisher: "Hyperconnect", hosts: ["hyperconnect.com"] },
  { category: "global_bigtech", tier: "P2", publisher: "Stripe Blog", hosts: ["stripe.com"] },
  { category: "global_strategy", tier: "P2", publisher: "Reforge Blog", hosts: ["reforge.com"] },
  { category: "global_aggregator", tier: "P2", publisher: "InfoQ", hosts: ["infoq.com"] },
  { category: "global_aggregator", tier: "P2", publisher: "TechCrunch", hosts: ["techcrunch.com"] },
];

const CATEGORY_LABELS: Record<string, string> = {
  kr_super_app: "KR Super App/PM",
  kr_engineering_core: "KR Engineering Core",
  kr_mobility: "KR Mobility/Travel",
  kr_ai_growth: "KR AI/Growth",
  kr_aggregator: "KR Curation",
  global_bigtech: "Global BigTech",
  global_ai: "Global AI",
  global_strategy: "Global Product/Strategy",
  global_aggregator: "Global Aggregator",
  uncategorized: "Uncategorized",
};

const CATEGORY_EMOJIS: Record<string, string> = {
  kr_super_app: "📱",
  kr_engineering_core: "🛠️",
  kr_mobility: "🚗",
  kr_ai_growth: "🤖",
  kr_aggregator: "🧭",
  global_bigtech: "🌐",
  global_ai: "🧠",
  global_strategy: "📈",
  global_aggregator: "🗞️",
  uncategorized: "📎",
};

function hostFromUrl(rawUrl: string): string {
  try {
    return new URL(rawUrl).hostname.trim().toLowerCase();
  } catch {
    return "";
  }
}

function matchHost(host: string, candidate: string): boolean {
  if (!host || !candidate) {
    return false;
  }
  return host === candidate || host.endsWith(`.${candidate}`);
}

export function inferSourceMeta(params: { url: string; publisher?: string }): {
  domain?: string;
  publisher?: string;
  category?: string;
  priorityTier?: SourceTier;
} {
  const domain = hostFromUrl(params.url);
  if (!domain) {
    return {};
  }

  for (const rule of SOURCE_RULES) {
    if (rule.hosts.some((host) => matchHost(domain, host))) {
      return {
        domain,
        publisher: params.publisher?.trim() || rule.publisher,
        category: rule.category,
        priorityTier: rule.tier,
      };
    }
  }

  return {
    domain,
    publisher: params.publisher?.trim() || domain,
    category: "uncategorized",
    priorityTier: "P2",
  };
}

export function sourceCategoryLabel(category?: string): string {
  if (!category) {
    return CATEGORY_LABELS.uncategorized;
  }
  return CATEGORY_LABELS[category] ?? category;
}

export function sourceCategoryEmoji(category?: string): string {
  if (!category) {
    return CATEGORY_EMOJIS.uncategorized;
  }
  return CATEGORY_EMOJIS[category] ?? CATEGORY_EMOJIS.uncategorized;
}

export function annotateSourceRefs(items: SourceRef[]): SourceRef[] {
  return items.map((item) => {
    const inferred = inferSourceMeta({ url: item.url, publisher: item.publisher });
    return {
      ...item,
      publisher: item.publisher ?? inferred.publisher,
      domain: item.domain ?? inferred.domain,
      category: item.category ?? inferred.category,
      priorityTier: item.priorityTier ?? inferred.priorityTier,
    };
  });
}
