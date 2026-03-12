from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


SourceTier = str


@dataclass(frozen=True)
class SourceRule:
    category: str
    tier: SourceTier
    publisher: str
    hosts: tuple[str, ...]


SOURCE_RULES: tuple[SourceRule, ...] = (
    # P0
    SourceRule("kr_super_app", "P0", "Toss Tech", ("toss.tech",)),
    SourceRule("kr_engineering_core", "P0", "Naver D2", ("d2.naver.com",)),
    SourceRule("kr_engineering_core", "P0", "Kakao Tech", ("tech.kakao.com",)),
    SourceRule("kr_aggregator", "P0", "GeekNews", ("news.hada.io",)),
    SourceRule("global_ai", "P0", "OpenAI News", ("openai.com",)),
    SourceRule("global_ai", "P0", "Anthropic News", ("anthropic.com",)),
    SourceRule("global_bigtech", "P0", "Cloudflare Blog", ("blog.cloudflare.com",)),
    SourceRule("global_bigtech", "P0", "AWS News Blog", ("aws.amazon.com",)),
    SourceRule("global_aggregator", "P0", "Hacker News", ("news.ycombinator.com",)),
    # P1
    SourceRule("kr_super_app", "P1", "Woowahan Tech", ("techblog.woowahan.com",)),
    SourceRule("kr_super_app", "P1", "Karrot Tech", ("daangn.com", "karrot.com")),
    SourceRule("kr_mobility", "P1", "MyRealTrip Tech", ("blog.myrealtrip.com",)),
    SourceRule("kr_mobility", "P1", "Socar Tech", ("tech.socarcorp.kr",)),
    SourceRule("kr_ai_growth", "P1", "Upstage", ("upstage.ai",)),
    SourceRule("kr_ai_growth", "P1", "AB180", ("blog.ab180.co",)),
    SourceRule("global_bigtech", "P1", "Netflix TechBlog", ("netflixtechblog.com",)),
    SourceRule("global_bigtech", "P1", "Airbnb Engineering", ("airbnb.tech",)),
    SourceRule("global_bigtech", "P1", "Uber Engineering", ("uber.com",)),
    SourceRule("global_ai", "P1", "Hugging Face Blog", ("huggingface.co",)),
    SourceRule("global_ai", "P1", "Google DeepMind Blog", ("deepmind.google",)),
    SourceRule("global_strategy", "P1", "Stratechery", ("stratechery.com",)),
    SourceRule("global_strategy", "P1", "Lenny's Newsletter", ("lennysnewsletter.com",)),
    # P2
    SourceRule("kr_super_app", "P2", "LINE Engineering", ("engineering.linecorp.com",)),
    SourceRule("kr_mobility", "P2", "Yanolja Tech", ("yanolja.github.io",)),
    SourceRule("kr_mobility", "P2", "Tmap Mobility Tech", ("tmapmobility.com",)),
    SourceRule("kr_ai_growth", "P2", "Hyperconnect", ("hyperconnect.com",)),
    SourceRule("global_bigtech", "P2", "Stripe Blog", ("stripe.com",)),
    SourceRule("global_strategy", "P2", "Reforge Blog", ("reforge.com",)),
    SourceRule("global_aggregator", "P2", "InfoQ", ("infoq.com",)),
    SourceRule("global_aggregator", "P2", "TechCrunch", ("techcrunch.com",)),
)

CATEGORY_LABELS: dict[str, str] = {
    "kr_super_app": "KR Super App/PM",
    "kr_engineering_core": "KR Engineering Core",
    "kr_mobility": "KR Mobility/Travel",
    "kr_ai_growth": "KR AI/Growth",
    "kr_aggregator": "KR Curation",
    "global_bigtech": "Global BigTech",
    "global_ai": "Global AI",
    "global_strategy": "Global Product/Strategy",
    "global_aggregator": "Global Aggregator",
    "uncategorized": "Uncategorized",
}

CATEGORY_EMOJIS: dict[str, str] = {
    "kr_super_app": "📱",
    "kr_engineering_core": "🛠️",
    "kr_mobility": "🚗",
    "kr_ai_growth": "🤖",
    "kr_aggregator": "🧭",
    "global_bigtech": "🌐",
    "global_ai": "🧠",
    "global_strategy": "📈",
    "global_aggregator": "🗞️",
    "uncategorized": "📎",
}


def source_category_label(category: str | None) -> str:
    if not category:
        return CATEGORY_LABELS["uncategorized"]
    return CATEGORY_LABELS.get(category, category)


def source_category_emoji(category: str | None) -> str:
    if not category:
        return CATEGORY_EMOJIS["uncategorized"]
    return CATEGORY_EMOJIS.get(category, CATEGORY_EMOJIS["uncategorized"])


def _host_from_url(raw_url: str) -> str:
    try:
        host = urlparse(raw_url).hostname
    except Exception:  # noqa: BLE001
        return ""
    return (host or "").strip().lower()


def _match_host(host: str, candidate: str) -> bool:
    if not host or not candidate:
        return False
    return host == candidate or host.endswith(f".{candidate}")


def infer_source_meta(url: str, publisher: str | None = None) -> dict[str, str]:
    domain = _host_from_url(url)
    if not domain:
        return {}

    for rule in SOURCE_RULES:
        if any(_match_host(domain, candidate) for candidate in rule.hosts):
            return {
                "domain": domain,
                "publisher": (publisher or "").strip() or rule.publisher,
                "category": rule.category,
                "priorityTier": rule.tier,
            }

    return {
        "domain": domain,
        "publisher": (publisher or "").strip() or domain,
        "category": "uncategorized",
        "priorityTier": "P2",
    }


def annotate_source_refs(items: list[dict[str, object]]) -> list[dict[str, object]]:
    annotated: list[dict[str, object]] = []
    for item in items:
        source = dict(item)
        meta = infer_source_meta(str(source.get("url", "")), str(source.get("publisher", "")) or None)
        if source.get("publisher") in (None, "") and meta.get("publisher"):
            source["publisher"] = meta["publisher"]
        if source.get("domain") in (None, "") and meta.get("domain"):
            source["domain"] = meta["domain"]
        if source.get("category") in (None, "") and meta.get("category"):
            source["category"] = meta["category"]
        if source.get("priorityTier") in (None, "") and meta.get("priorityTier"):
            source["priorityTier"] = meta["priorityTier"]
        annotated.append(source)
    return annotated
