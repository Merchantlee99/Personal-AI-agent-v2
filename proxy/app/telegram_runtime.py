from __future__ import annotations

import hmac
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Any

from fastapi import Request

from .google_calendar import (
    is_google_calendar_enabled,
    is_google_calendar_readonly,
    list_google_today_events,
)
from .orch_store import (
    apply_clio_note_suggestion,
    confirm_clio_claim_review,
    create_inbox_task,
    find_event_by_id,
    get_approval_request,
    list_new_clio_claim_review_alerts,
    list_new_clio_note_suggestion_alerts,
    list_pending_clio_claim_reviews,
    list_pending_clio_note_suggestions,
    mark_clio_alert_sent,
)
from .role_runtime import read_bool_env, read_int_env
from .telegram_bridge import (
    create_clio_claim_review_keyboard,
    create_clio_note_suggestion_keyboard,
    render_clio_claim_review_text,
    render_clio_note_suggestion_text,
    send_telegram_message,
)

logger = logging.getLogger("nanoclaw.llm_proxy")

DIRECT_CALLBACK_ACTIONS = {"clio_save", "hermes_deep_dive", "minerva_insight"}
SYSTEM_CALLBACK_ACTIONS = {"clio_confirm_knowledge", "clio_apply_suggestion", "clio_dismiss_suggestion"}
INTERNAL_APPROVAL_ACTIONS = {"approval_yes", "approval_no", "approval_commit"}
TEXT_RATE_WINDOW: dict[str, dict[str, int]] = {}
CLIO_ALERT_INTERVAL_SEC = read_int_env("CLIO_ALERT_SCAN_INTERVAL_SEC", 30, minimum=10)
CLIO_ALERTS_ENABLED = read_bool_env("CLIO_TELEGRAM_ALERT_ENABLED", True)
_CLIO_ALERT_THREAD_STARTED = False


def parse_allowlist(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {token.strip() for token in raw.split(",") if token.strip()}


def verify_webhook_secret(request: Request) -> bool:
    expected = (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip()
    if not expected:
        return True
    incoming = (request.headers.get("x-telegram-bot-api-secret-token") or "").strip()
    return bool(incoming) and hmac.compare_digest(incoming, expected)


def verify_allowlist(update_source: dict[str, Any]) -> tuple[bool, str, str, str]:
    allowed_users = parse_allowlist(os.getenv("TELEGRAM_ALLOWED_USER_IDS"))
    allowed_chats = parse_allowlist(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))

    user_id = str(update_source.get("userId", "")).strip()
    chat_id = str(update_source.get("chatId", "")).strip()

    if allowed_users:
        if not user_id:
            return False, "missing_user_id", user_id, chat_id
        if user_id not in allowed_users:
            return False, "user_not_allowed", user_id, chat_id
    if allowed_chats:
        if not chat_id:
            return False, "missing_chat_id", user_id, chat_id
        if chat_id not in allowed_chats:
            return False, "chat_not_allowed", user_id, chat_id
    return True, "", user_id, chat_id


def verify_approval_source(approval: dict[str, Any], *, user_id: str, chat_id: str) -> tuple[bool, str]:
    approval_user_id = str(approval.get("requestedByUserId", "")).strip()
    approval_chat_id = str(approval.get("chatId", "")).strip()
    if approval_user_id and user_id and approval_user_id != user_id:
        return False, "approval_user_mismatch"
    if approval_chat_id and chat_id and approval_chat_id != chat_id:
        return False, "approval_chat_mismatch"
    return True, ""


def is_allowed_action(action: str) -> bool:
    if action in INTERNAL_APPROVAL_ACTIONS:
        return True
    if action in SYSTEM_CALLBACK_ACTIONS:
        return True
    configured = parse_allowlist(os.getenv("TELEGRAM_ALLOWED_CALLBACK_ACTIONS"))
    if not configured:
        return action in DIRECT_CALLBACK_ACTIONS
    return action in configured


def approval_queue_enabled() -> bool:
    return read_bool_env("TELEGRAM_APPROVAL_QUEUE_ENABLED", True)


def requires_approval(action: str) -> bool:
    if not approval_queue_enabled():
        return False
    if action in SYSTEM_CALLBACK_ACTIONS:
        return True
    configured = parse_allowlist(os.getenv("TELEGRAM_APPROVAL_REQUIRED_ACTIONS"))
    if not configured:
        return action in DIRECT_CALLBACK_ACTIONS
    return action in configured


def check_text_rate_limit(chat_id: str) -> tuple[bool, int]:
    window_sec = read_int_env("TELEGRAM_TEXT_RATE_LIMIT_WINDOW_SEC", 60, 1)
    max_per_window = read_int_env("TELEGRAM_TEXT_RATE_LIMIT_MAX", 12, 1)
    now_ms = int(datetime.now().timestamp() * 1000)
    window_start = now_ms - (now_ms % (window_sec * 1000))
    entry = TEXT_RATE_WINDOW.get(chat_id)
    if not entry or entry.get("windowStart") != window_start:
        TEXT_RATE_WINDOW[chat_id] = {"windowStart": window_start, "count": 1}
        return True, 0
    if entry.get("count", 0) >= max_per_window:
        retry_after = max(1, int((window_start + window_sec * 1000 - now_ms + 999) / 1000))
        return False, retry_after
    entry["count"] = entry.get("count", 0) + 1
    TEXT_RATE_WINDOW[chat_id] = entry
    return True, 0


def compact_line(value: str, max_len: int) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    if len(normalized) <= max_len:
        return normalized
    return f"{normalized[: max_len - 1].rstrip()}…"


def format_telegram_plain_text(value: str, max_len: int) -> str:
    lines = []
    for raw in value.replace("\r", "").replace("**", "").split("\n"):
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", raw).strip()
        line = re.sub(r'^["“”\'`]+|["“”\'`]+$', "", line)
        line = re.sub(r"\s+", " ", line).strip()
        lines.append(line)
    normalized = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if len(normalized) <= max_len:
        return normalized
    return f"{normalized[: max_len - 1].rstrip()}…"


def google_calendar_auto_attach_enabled() -> bool:
    return read_bool_env("GOOGLE_CALENDAR_ATTACH_TO_MORNING_BRIEFING", True)


def render_gcal_status_text(status: dict[str, Any]) -> str:
    enabled = bool(status.get("enabled"))
    readonly = bool(status.get("readonly"))
    connected = bool(status.get("connected"))
    token_expired = bool(status.get("tokenExpired"))
    refresh_available = bool(status.get("refreshAvailable"))
    scope = str(status.get("scope") or "-")
    updated_at = str(status.get("tokenUpdatedAt") or "-")
    expires_at = str(status.get("tokenExpiresAt") or "-")
    return (
        "📅 Google Calendar 상태\n"
        f"• 활성화: {'ON' if enabled else 'OFF'}\n"
        f"• 연결: {'연결됨' if connected else '미연결'}\n"
        f"• 권한: {'read-only' if readonly else '확장 권한'}\n"
        f"• 토큰 상태: {'만료됨(자동갱신 가능)' if token_expired and refresh_available else '만료됨' if token_expired else '정상'}\n"
        f"• scope: {scope}\n"
        f"• 토큰 갱신: {updated_at}\n"
        f"• 토큰 만료: {expires_at}"
    )


def render_gcal_today_text(today: dict[str, Any], limit: int = 8) -> str:
    events = today.get("events") if isinstance(today, dict) else []
    if not isinstance(events, list):
        events = []
    if not events:
        return "📅 오늘 일정이 없습니다."

    lines = ["📅 오늘 일정 요약"]
    for index, event in enumerate(events[: max(1, limit)], start=1):
        if not isinstance(event, dict):
            continue
        summary = compact_line(str(event.get("summary") or "(제목 없음)"), 80)
        start = str(event.get("start") or "-")
        end = str(event.get("end") or "-")
        lines.append(f"{index}. {summary} ({start} ~ {end})")
    if len(events) > limit:
        lines.append(f"… 외 {len(events) - limit}건")
    return "\n".join(lines)


def build_calendar_briefing_payload_for_dispatch() -> dict[str, Any] | None:
    if not is_google_calendar_enabled():
        return None
    if not is_google_calendar_readonly():
        return None
    if not google_calendar_auto_attach_enabled():
        return None
    try:
        today = list_google_today_events()
    except Exception as exc:  # noqa: BLE001
        logger.warning("google_calendar_attach_failed detail=%s", exc)
        return None
    events = today.get("events") if isinstance(today, dict) else []
    if not isinstance(events, list):
        return None
    if not events:
        return {"summary": "오늘 등록된 일정이 없습니다.", "items": []}
    compact_items = []
    for item in events[:5]:
        if not isinstance(item, dict):
            continue
        start = str(item.get("start") or "")
        end = str(item.get("end") or "")
        time_label = f"{start} ~ {end}" if start or end else "시간 미정"
        compact_items.append(
            {
                "title": compact_line(str(item.get("summary") or "(제목 없음)"), 80),
                "timeLabel": compact_line(time_label, 64),
            }
        )
    return {"summary": f"오늘 일정 {len(events)}건", "items": compact_items}


def execute_inline_action(action: str, event: dict[str, Any]) -> dict[str, Any]:
    source_refs = [
        {"title": str(item.get("title", "")), "url": str(item.get("url", ""))}
        for item in (event.get("sourceRefs") or [])
        if isinstance(item, dict)
    ]
    if action == "clio_save":
        inbox = create_inbox_task(
            target_agent_id="clio",
            reason="telegram_inline_clio_obsidian_save",
            topic_key=str(event.get("topicKey", "")),
            title=str(event.get("title", "")),
            summary=(
                "다음 내용을 Clio Obsidian 저장 포맷으로 정리해 저장하세요.\n"
                f"- 핵심 요약: {event.get('summary', '')}\n"
                "- 필수 출력: 태그, 관련 노트 링크, 출처 URL, notebooklm_ready 메타"
            ),
            source_refs=source_refs,
        )
        return {"action": action, "eventId": event.get("eventId"), "inbox": inbox, "callbackText": "Clio 옵시디언 저장 요청을 접수했습니다."}

    if action == "hermes_deep_dive":
        inbox = create_inbox_task(
            target_agent_id="hermes",
            reason="telegram_inline_hermes_find_more",
            topic_key=str(event.get("topicKey", "")),
            title=str(event.get("title", "")),
            summary=(
                "다음 주제와 직접 관련된 뉴스/아티클/트렌드 신호를 더 찾아주세요.\n"
                f"- 기준 요약: {event.get('summary', '')}\n"
                "- 역할 제한: 사실/근거 수집만 수행하고, 최종 판단·전략 결론은 작성하지 마세요.\n"
                "- 요청 출력: 관련 출처 5개 이상, 상충 관점 1개 이상, 핵심 변화 요약(데이터 중심)\n"
                "- 후속 처리: 처리 완료 후 Minerva 인사이트 분석 태스크가 자동 생성됩니다."
            ),
            source_refs=source_refs,
        )
        return {
            "action": action,
            "eventId": event.get("eventId"),
            "inbox": inbox,
            "callbackText": "Hermes 근거 수집 요청을 접수했습니다. 완료 후 Minerva 분석이 자동 연결됩니다.",
        }

    inbox = create_inbox_task(
        target_agent_id="minerva",
        reason="telegram_inline_minerva_insight",
        topic_key=str(event.get("topicKey", "")),
        title=str(event.get("title", "")),
        summary=(
            "다음 주제에 대해 Minerva 2차적 사고 기반 인사이트 분석을 수행하세요.\n"
            f"- 핵심 변화(1차): {event.get('summary', '')}\n"
            "- 2차 분석: 원인-결과 연결고리, 파급 영향도, 리스크/기회 분해\n"
            "- 요청 출력: 우선순위 액션 3개"
        ),
        source_refs=source_refs,
    )
    return {"action": action, "eventId": event.get("eventId"), "inbox": inbox, "callbackText": "Minerva 2차 인사이트 분석 요청을 접수했습니다."}


def execute_approval_request(approval: dict[str, Any], *, actor_user_id: str) -> dict[str, Any]:
    payload = approval.get("payload") if isinstance(approval.get("payload"), dict) else {}
    target_type = str(payload.get("targetType") or "").strip()

    if target_type == "clio_note_suggestion" or str(approval.get("action") or "") == "clio_apply_suggestion":
        suggestion_id = str(payload.get("suggestionId") or "").strip()
        if not suggestion_id:
            return {"ok": False, "reason": "clio_note_suggestion_id_missing"}
        applied = apply_clio_note_suggestion(suggestion_id, actor_user_id)
        if not applied:
            return {"ok": False, "reason": "clio_note_suggestion_not_found"}
        return {
            "ok": True,
            "action": approval.get("action"),
            "targetType": "clio_note_suggestion",
            "noteSuggestion": applied,
            "callbackText": "Clio 노트 제안을 review 상태로 반영했습니다.",
        }

    if target_type == "clio_claim_review" or str(approval.get("action") or "") == "clio_confirm_knowledge":
        review_id = str(payload.get("reviewId") or "").strip()
        if not review_id:
            return {"ok": False, "reason": "claim_review_id_missing"}
        confirmed = confirm_clio_claim_review(review_id, actor_user_id)
        if not confirmed:
            return {"ok": False, "reason": "claim_review_not_found"}
        return {
            "ok": True,
            "action": approval.get("action"),
            "targetType": "clio_claim_review",
            "claimReview": confirmed,
            "callbackText": "Clio 지식 노트를 confirmed 상태로 승인했습니다.",
        }

    event = find_event_by_id(str(approval.get("eventId", "")))
    if not event:
        return {"ok": False, "reason": "event_not_found"}
    execution = execute_inline_action(str(approval.get("action", "")), event)
    return {"ok": True, "targetType": "event", **execution}


def _auto_alert_chat_id() -> str:
    return str(os.getenv("TELEGRAM_CHAT_ID") or "").strip()


def dispatch_pending_clio_alerts_once() -> None:
    chat_id = _auto_alert_chat_id()
    if not CLIO_ALERTS_ENABLED or not chat_id:
        return

    for review in list_new_clio_claim_review_alerts(limit=3):
        review_id = str(review.get("id") or "").strip()
        if not review_id:
            continue
        send_telegram_message(
            {
                "chat_id": chat_id,
                "text": render_clio_claim_review_text(review, pending_count=len(list_pending_clio_claim_reviews(limit=200)), mode="alert"),
                "reply_markup": create_clio_claim_review_keyboard(review_id),
                "disable_web_page_preview": True,
            }
        )
        mark_clio_alert_sent("claim_review", review_id)

    for suggestion in list_new_clio_note_suggestion_alerts(limit=3):
        suggestion_id = str(suggestion.get("id") or "").strip()
        if not suggestion_id:
            continue
        send_telegram_message(
            {
                "chat_id": chat_id,
                "text": render_clio_note_suggestion_text(
                    suggestion,
                    pending_count=len(list_pending_clio_note_suggestions(limit=200)),
                    mode="alert",
                ),
                "reply_markup": create_clio_note_suggestion_keyboard(suggestion_id),
                "disable_web_page_preview": True,
            }
        )
        mark_clio_alert_sent("note_suggestion", suggestion_id, fingerprint=str(suggestion.get("suggestionFingerprint") or ""))


def _clio_alert_loop() -> None:
    while True:
        try:
            dispatch_pending_clio_alerts_once()
        except Exception:  # noqa: BLE001
            logger.exception("clio alert dispatch loop failed")
        time.sleep(CLIO_ALERT_INTERVAL_SEC)


def start_clio_alert_loop() -> None:
    global _CLIO_ALERT_THREAD_STARTED
    if _CLIO_ALERT_THREAD_STARTED:
        return
    if not CLIO_ALERTS_ENABLED or not _auto_alert_chat_id():
        return
    thread = threading.Thread(target=_clio_alert_loop, name="clio-alert-loop", daemon=True)
    thread.start()
    _CLIO_ALERT_THREAD_STARTED = True
