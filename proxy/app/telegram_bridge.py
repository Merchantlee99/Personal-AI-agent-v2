from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .source_taxonomy import source_category_emoji, source_category_label

PRIORITY_EMOJI = {
    "critical": "🚨",
    "high": "🔔",
    "normal": "🧭",
    "low": "📝",
}

TIER_STYLES = {
    "P0": {"header": "⚡ P0 즉시 브리핑", "summaryMaxLines": 2, "insightMaxLines": 1, "maxSources": 2},
    "P1": {"header": "🧠 P1 분석 브리핑", "summaryMaxLines": 3, "insightMaxLines": 2, "maxSources": 3},
    "P2": {"header": "🗂️ P2 스캔 브리핑", "summaryMaxLines": 2, "insightMaxLines": 2, "maxSources": 2},
}

TIER_TRANSLATION_POLICY = {
    "P0": {"translateSummary": True, "summaryCharLimit": 480, "maxSnippetTranslations": 2, "snippetCharLimit": 180},
    "P1": {"translateSummary": True, "summaryCharLimit": 420, "maxSnippetTranslations": 1, "snippetCharLimit": 160},
    "P2": {"translateSummary": False, "summaryCharLimit": 0, "maxSnippetTranslations": 0, "snippetCharLimit": 0},
}


def clean_line(value: str) -> str:
    text = str(value or "").replace("\r", "")
    text = text.replace("\\n", "\n").replace("**", "")
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r'^["“”\'`]+|["“”\'`]+$', "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def short_text(value: str, limit: int) -> str:
    line = clean_line(value)
    if len(line) <= limit:
        return line
    return f"{line[: limit - 1].rstrip()}…"


def trim_telegram_text(value: str, max_len: int = 3700) -> str:
    normalized = re.sub(r"\n{3,}", "\n\n", value).strip()
    if len(normalized) <= max_len:
        return normalized
    return f"{normalized[: max_len - 1].rstrip()}…"


def normalize_tier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip().upper()
    if token in {"P0", "P1", "P2"}:
        return token
    return None


def infer_tier(event: dict[str, Any]) -> str:
    for tag in event.get("tags") or []:
        if not isinstance(tag, str):
            continue
        match = re.match(r"^tier:(p[0-2])$", tag.strip(), re.I)
        if match:
            parsed = normalize_tier(match.group(1))
            if parsed:
                return parsed

    payload_tier = normalize_tier((event.get("payload") or {}).get("priority_tier") if isinstance(event.get("payload"), dict) else None)
    source_tiers = [
        normalize_tier(source.get("priorityTier"))
        for source in (event.get("sourceRefs") or [])
        if isinstance(source, dict)
    ]
    source_tiers = [tier for tier in source_tiers if tier]
    if payload_tier:
        source_tiers.append(payload_tier)
    if source_tiers:
        rank = {"P0": 0, "P1": 1, "P2": 2}
        return sorted(source_tiers, key=lambda item: rank.get(item, 9))[0]

    priority = str(event.get("priority", "normal"))
    if priority == "critical":
        return "P0"
    if priority == "high":
        return "P1"
    return "P2"


def create_inline_keyboard(event_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "Clio, 옵시디언에 저장해", "callback_data": f"clio_save:{event_id}"}],
            [{"text": "Hermes, 더 찾아", "callback_data": f"hermes_deep_dive:{event_id}"}],
            [{"text": "Minerva, 인사이트 분석해", "callback_data": f"minerva_insight:{event_id}"}],
        ]
    }


def create_clio_claim_review_keyboard(review_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "Clio 지식 노트 승인 요청", "callback_data": f"clio_confirm_knowledge:{review_id}"}],
        ]
    }


def create_clio_note_suggestion_keyboard(suggestion_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "Clio 제안 적용 승인 요청", "callback_data": f"clio_apply_suggestion:{suggestion_id}"}],
            [{"text": "이 제안 보류", "callback_data": f"clio_dismiss_suggestion:{suggestion_id}"}],
        ]
    }


def approval_action_label(action: str) -> str:
    if action == "clio_save":
        return "Clio 저장"
    if action == "clio_confirm_knowledge":
        return "Clio 지식 노트 승인"
    if action == "clio_apply_suggestion":
        return "Clio 노트 제안 적용"
    if action == "hermes_deep_dive":
        return "Hermes 추가 수집"
    return "Minerva 인사이트 분석"


def create_approval_stage1_keyboard(approval_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "네, 진행", "callback_data": f"approval_yes:{approval_id}"},
                {"text": "아니요", "callback_data": f"approval_no:{approval_id}"},
            ]
        ]
    }


def create_approval_stage2_keyboard(approval_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "최종 승인", "callback_data": f"approval_commit:{approval_id}"},
                {"text": "취소", "callback_data": f"approval_no:{approval_id}"},
            ]
        ]
    }


def render_approval_stage1_text(approval: dict[str, Any]) -> str:
    return trim_telegram_text(
        "\n".join(
            [
                "⚠️ 승인 필요",
                "",
                f"- 액션: {approval_action_label(str(approval.get('action', '')))}",
                f"- 주제: {short_text(str(approval.get('topicKey', '')), 70)}",
                f"- 제목: {short_text(str(approval.get('eventTitle', '')), 72)}",
                "- 1차 확인: 아래 버튼으로 승인 또는 취소를 선택하세요.",
                f"- 만료: {short_text(str(approval.get('expiresAt', '')), 36)}",
            ]
        ),
        1200,
    )


def render_approval_stage2_text(approval: dict[str, Any]) -> str:
    return trim_telegram_text(
        "\n".join(
            [
                "⚠️ 최종 승인 필요",
                "",
                f"- 액션: {approval_action_label(str(approval.get('action', '')))}",
                f"- 주제: {short_text(str(approval.get('topicKey', '')), 70)}",
                f"- 제목: {short_text(str(approval.get('eventTitle', '')), 72)}",
                "- 실수 방지: 정말 진행할지 한 번 더 확인하세요.",
                f"- 만료: {short_text(str(approval.get('expiresAt', '')), 36)}",
            ]
        ),
        1200,
    )


def render_clio_claim_review_text(review: dict[str, Any], *, pending_count: int, mode: str = "queue") -> str:
    header = "🧠 Clio knowledge review" if mode == "queue" else "🧠 Clio 지식 검토 알림"
    return trim_telegram_text(
        "\n".join(
            [
                header,
                "",
                f"- 제목: {short_text(str(review.get('title', '')), 72)}",
                f"- 주제: {short_text(str(review.get('topicKey', '')), 70)}",
                f"- 노트 경로: {short_text(str(review.get('vaultFile', '')), 88)}",
                (
                    f"- 프로젝트 링크: {', '.join(review.get('projectLinks', [])[:3])}"
                    if isinstance(review.get("projectLinks"), list) and review.get("projectLinks")
                    else "- 프로젝트 링크: 없음"
                ),
                (
                    f"- MOC 후보: {', '.join(review.get('mocCandidates', [])[:3])}"
                    if isinstance(review.get("mocCandidates"), list) and review.get("mocCandidates")
                    else "- MOC 후보: 없음"
                ),
                f"- 대기 건수: {pending_count}",
                "- 이 노트는 현재 draft 상태입니다. 승인 시 confirmed로 승격됩니다." if mode == "queue" else "- 새 knowledge claim draft가 대기열에 들어왔습니다. 검토 후 승인 여부를 정하세요.",
            ]
        ),
        1400,
    )


def render_clio_note_suggestion_text(suggestion: dict[str, Any], *, pending_count: int, mode: str = "queue") -> str:
    action = clean_line(str(suggestion.get("noteAction") or "create"))
    target = clean_line(str(suggestion.get("updateTarget") or ""))
    merge_candidates = suggestion.get("mergeCandidates") if isinstance(suggestion.get("mergeCandidates"), list) else []
    diff_summary = suggestion.get("diffSummary") if isinstance(suggestion.get("diffSummary"), list) else []
    suggestion_reasons = suggestion.get("suggestionReasons") if isinstance(suggestion.get("suggestionReasons"), list) else []
    suggestion_score = suggestion.get("suggestionScore")
    lines = [
        "🧩 Clio note suggestion" if mode == "queue" else "🧩 Clio 노트 연결 제안 알림",
        "",
        f"- 제목: {short_text(str(suggestion.get('title', '')), 72)}",
        f"- 제안 유형: {action}",
        f"- 노트 경로: {short_text(str(suggestion.get('vaultFile', '')), 88)}",
    ]
    if target:
        lines.append(f"- 업데이트 대상: {short_text(target, 88)}")
    elif merge_candidates:
        lines.append(f"- 병합 후보: {', '.join(merge_candidates[:3])}")
    else:
        lines.append("- 병합 후보: 없음")
    if isinstance(suggestion_score, (int, float)):
        lines.append(f"- 제안 점수: {float(suggestion_score):.2f}")
    if suggestion_reasons:
        lines.append("- 제안 근거:")
        for item in suggestion_reasons[:3]:
            lines.append(f"  • {short_text(str(item), 96)}")
    if diff_summary:
        lines.append("- 변경 요약:")
        for item in diff_summary[:3]:
            lines.append(f"  • {short_text(str(item), 96)}")
    lines.extend(
        [
            f"- 대기 건수: {pending_count}",
            (
                "- 승인 시 기존 노트에는 안전한 링크/주석만 추가하고, draft note는 review 상태로 전환됩니다."
                if mode == "queue"
                else "- 새 note를 만들지 않고 기존 지식과 연결·업데이트할지 확인이 필요합니다."
            ),
        ]
    )
    return trim_telegram_text("\n".join(lines), 1400)


def summary_lines(value: str, max_lines: int) -> list[str]:
    lines = []
    for line in clean_line(value).split("\n"):
        compact = short_text(line, 140)
        if compact:
            lines.append(compact)
    return lines[:max_lines]


def build_topics(event: dict[str, Any], tier: str, max_topics: int) -> list[dict[str, Any]]:
    refs = event.get("sourceRefs") or []
    topics: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        title = str(ref.get("title", "")).strip()
        url = str(ref.get("url", "")).strip()
        if not title:
            continue
        key = f"{url}|{title}"
        if key in seen:
            continue
        seen.add(key)
        topics.append(
            {
                "title": title,
                "url": url,
                "snippet": ref.get("snippet"),
                "tier": normalize_tier(ref.get("priorityTier")) or tier,
                "categoryLabel": source_category_label(ref.get("category") if isinstance(ref.get("category"), str) else None),
                "emoji": source_category_emoji(ref.get("category") if isinstance(ref.get("category"), str) else None),
            }
        )
        if len(topics) >= max_topics:
            break

    if topics:
        return topics
    return [
        {
            "title": str(event.get("title", "")),
            "url": "",
            "snippet": str(event.get("summary", "")),
            "tier": tier,
            "categoryLabel": "Uncategorized",
            "emoji": "📎",
        }
    ]


def build_summary_by_topic(event: dict[str, Any], topics: list[dict[str, Any]], max_lines: int) -> list[str]:
    fallback = summary_lines(str(event.get("summary", "")), max(1, max_lines))
    if len(topics) <= 1:
        if topics and topics[0].get("snippet"):
            return [f"- {short_text(clean_line(str(topics[0]['snippet'])), 140)}"]
        return [f"- {line}" for line in fallback] if fallback else ["- 요약 없음"]

    rows: list[str] = []
    for index in range(min(max_lines, len(topics))):
        topic = topics[index]
        snippet = topic.get("snippet")
        summary = (
            short_text(clean_line(str(snippet)), 110)
            if snippet
            else fallback[index % max(1, len(fallback))] if fallback else "요약 없음"
        )
        rows.append(f"- {topic['emoji']} {short_text(str(topic['title']), 32)}: {summary}")
    return rows


def build_sources(topics: list[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    for index, topic in enumerate(topics):
        if topic.get("url"):
            rows.append(
                f"- {index + 1}) [{topic['tier']}] {topic['emoji']} {topic['categoryLabel']} | {short_text(str(topic['title']), 52)}\n"
                f"  {topic['url']}"
            )
        else:
            rows.append(f"- {index + 1}) [{topic['tier']}] {topic['emoji']} {topic['categoryLabel']} | 내부 이벤트")
    return rows


def build_insight_section(event: dict[str, Any], style: dict[str, Any], topics: list[dict[str, Any]]) -> list[str]:
    hint = str(event.get("insightHint") or "연결 가능한 신호를 확인했고, 다음 액션 우선순위를 조정하는 것이 좋겠습니다.")
    lines = [f"- {line}" for line in summary_lines(hint, int(style["insightMaxLines"]))]
    focus = [f"{topic['emoji']} {short_text(str(topic['title']), 22)}" for topic in topics[:2]]
    if focus:
        lines.append(f"- 우선 분석 대상: {' / '.join(focus)}")
    categories = list(dict.fromkeys([f"{topic['emoji']} {topic['categoryLabel']}" for topic in topics]))
    if len(categories) >= 2:
        lines.append(f"- 연관성: {categories[0]} ↔ {categories[1]} 축의 동시 변화가 보입니다.")
    return lines


def render_minerva_telegram_text(event: dict[str, Any], calendar_briefing: dict[str, Any] | None = None) -> str:
    priority = str(event.get("priority", "normal"))
    emoji = PRIORITY_EMOJI.get(priority, "🧭")
    tier = infer_tier(event)
    style = TIER_STYLES[tier]
    topics = build_topics(event, tier, int(style["maxSources"]))
    summary = build_summary_by_topic(event, topics, int(style["summaryMaxLines"]))
    sources = build_sources(topics)
    insights = build_insight_section(event, style, topics)

    lines = [
        f"{emoji} Minerva 브리핑 · {style['header']}",
        "",
        "🧩 주제",
        *[f"- {topic['emoji']} {short_text(str(topic['title']), 64)}" for topic in topics],
        "",
        "📌 핵심 요약",
        *summary,
    ]

    if calendar_briefing:
        lines.extend(["", "📅 오늘 일정", f"오늘 일정: {short_text(str(calendar_briefing.get('summary', '')), 120)}"])
        for item in (calendar_briefing.get("items") or [])[:3]:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('timeLabel', '시간미정')} {short_text(str(item.get('title', '')), 56)}")

    lines.extend(["", "🔎 출처", *sources, "", "🧠 Minerva 인사이트", *(insights or ["- 인사이트 힌트 없음"])])
    return trim_telegram_text("\n".join(lines))


def _contains_significant_latin(text: str) -> bool:
    latin_count = len(re.findall(r"[A-Za-z]", text))
    hangul_count = len(re.findall(r"[가-힣]", text))
    return latin_count >= 8 and latin_count > hangul_count


def should_translate_to_korean(text: str) -> bool:
    return _contains_significant_latin(clean_line(text))


def translate_to_korean(text: str) -> str:
    key = (os.getenv("DEEPL_API_KEY") or "").strip()
    if not key:
        return text
    api_base = (os.getenv("DEEPL_API_BASE") or "https://api-free.deepl.com").rstrip("/")
    target_lang = (os.getenv("DEEPL_TARGET_LANG") or "KO").strip().upper() or "KO"
    glossary_id = (os.getenv("DEEPL_GLOSSARY_ID") or "").strip()

    data = [("auth_key", key), ("text", text), ("target_lang", target_lang)]
    if glossary_id:
        data.append(("glossary_id", glossary_id))
    payload = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        f"{api_base}/v2/translate",
        data=payload,
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            raw = response.read().decode("utf-8")
        parsed = json.loads(raw)
        translations = parsed.get("translations")
        if isinstance(translations, list) and translations:
            translated = translations[0].get("text")
            if isinstance(translated, str) and translated.strip():
                return translated.strip()
    except Exception:  # noqa: BLE001
        return text
    return text


def _trim_for_translation(value: str, limit: int) -> str:
    normalized = clean_line(value)
    if limit <= 0 or len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1].rstrip()}…"


def localize_event_for_telegram(event: dict[str, Any]) -> dict[str, Any]:
    if not (os.getenv("DEEPL_API_KEY") or "").strip():
        return event

    tier = infer_tier(event)
    policy = TIER_TRANSLATION_POLICY[tier]
    if not policy["translateSummary"] and int(policy["maxSnippetTranslations"]) <= 0:
        return event

    localized = dict(event)
    if policy["translateSummary"]:
        candidate = _trim_for_translation(str(event.get("summary", "")), int(policy["summaryCharLimit"]))
        if should_translate_to_korean(candidate):
            localized["summary"] = translate_to_korean(candidate)

    translated_snippets = 0
    localized_refs: list[dict[str, Any]] = []
    for source in event.get("sourceRefs") or []:
        if not isinstance(source, dict):
            continue
        item = dict(source)
        snippet = str(item.get("snippet", "")).strip()
        if (
            snippet
            and translated_snippets < int(policy["maxSnippetTranslations"])
            and should_translate_to_korean(snippet)
        ):
            candidate = _trim_for_translation(snippet, int(policy["snippetCharLimit"]))
            item["snippet"] = translate_to_korean(candidate)
            translated_snippets += 1
        localized_refs.append(item)
    localized["sourceRefs"] = localized_refs
    return localized


def build_telegram_dispatch_payload(
    *,
    chat_id: str,
    event: dict[str, Any],
    calendar_briefing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    localized = localize_event_for_telegram(event)
    return {
        "chat_id": chat_id,
        "text": render_minerva_telegram_text(localized, calendar_briefing),
        "disable_web_page_preview": True,
        "reply_markup": create_inline_keyboard(str(localized.get("eventId", ""))),
    }


def _post_telegram_api(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        return {"ok": False, "reason": "telegram_token_missing"}

    base = (os.getenv("TELEGRAM_API_BASE_URL") or "https://api.telegram.org").rstrip("/")
    timeout_ms = max(2000, int(float(os.getenv("TELEGRAM_API_TIMEOUT_MS", "8000") or "8000")))
    endpoint = f"{base}/bot{token}/{method}"

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_ms / 1000) as response:
            status = int(response.status)
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="ignore")
        return {"ok": False, "reason": "telegram_api_failed", "status": int(error.code), "detail": detail}
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "reason": "telegram_api_failed", "detail": str(error)}

    if status < 200 or status >= 300:
        return {"ok": False, "reason": "telegram_api_failed", "status": status, "detail": raw}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "reason": "telegram_api_failed", "status": status, "detail": "invalid_telegram_api_response"}
    return {"ok": True, "reason": "ok", "response": parsed}


def send_telegram_message(payload: dict[str, Any]) -> dict[str, Any]:
    result = _post_telegram_api("sendMessage", payload)
    if not result.get("ok"):
        return {
            "sent": False,
            "reason": "telegram_token_missing"
            if result.get("reason") == "telegram_token_missing"
            else "telegram_send_failed",
            "status": result.get("status"),
            "detail": result.get("detail"),
        }
    return {"sent": True, "reason": "ok", "response": result.get("response")}


def send_telegram_text_message(*, chat_id: str, text: str, disable_web_page_preview: bool = True) -> dict[str, Any]:
    return send_telegram_message(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
    )


def answer_telegram_callback(*, callback_query_id: str, text: str, show_alert: bool = False) -> dict[str, Any]:
    result = _post_telegram_api(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_query_id,
            "text": short_text(text, 120),
            "show_alert": bool(show_alert),
        },
    )
    if not result.get("ok"):
        return {
            "ok": False,
            "reason": "telegram_token_missing"
            if result.get("reason") == "telegram_token_missing"
            else "telegram_answer_failed",
            "status": result.get("status"),
        }
    return {"ok": True}
