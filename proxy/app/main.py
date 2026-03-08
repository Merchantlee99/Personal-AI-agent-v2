from __future__ import annotations

import hmac
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .agents import AGENT_REGISTRY, normalize_agent_id
from .google_calendar import (
    build_google_oauth_authorization_url,
    consume_google_oauth_state,
    create_google_oauth_state,
    get_google_calendar_connection_status,
    is_google_calendar_enabled,
    is_google_calendar_readonly,
    list_google_today_events,
    save_google_token_from_code,
)
from .llm_client import FatalLLMError, RetryableLLMError, generate_agent_reply
from .models import AgentRequest, AgentResponse, ChatRequest, HistoryMessage, SearchRequest, SearchResponse
from .orch_contract import ORCHESTRATION_EVENT_SCHEMA_VERSION, validate_event_contract_v1
from .orch_policy import evaluate_dispatch_policy, get_dispatch_policy, get_journey_theme
from .orch_store import (
    append_agent_event,
    append_telegram_chat_history,
    approve_stage_one,
    apply_clio_note_suggestion,
    clear_telegram_chat_history,
    confirm_clio_claim_review,
    get_clio_knowledge_memory,
    get_clio_claim_review,
    get_clio_note_suggestion,
    create_approval_request,
    create_event_id,
    create_inbox_task,
    dismiss_clio_note_suggestion,
    find_event_by_id,
    get_approval_queue_stats,
    get_approval_request,
    get_cooldown,
    get_hermes_evidence_memory,
    get_minerva_working_memory,
    get_telegram_chat_history,
    list_pending_clio_claim_reviews,
    list_pending_clio_note_suggestions,
    list_new_clio_claim_review_alerts,
    list_new_clio_note_suggestion_alerts,
    list_agent_events,
    mark_clio_alert_sent,
    make_dedupe_key,
    mark_approval_executed,
    push_digest_item,
    reject_approval_request,
    render_clio_knowledge_memory_context,
    render_hermes_evidence_memory_context,
    render_minerva_working_memory_context,
)
from .search_client import SearchProviderError, get_search_results
from .security import verify_internal_request
from .source_taxonomy import annotate_source_refs
from .telegram_bridge import (
    answer_telegram_callback,
    build_telegram_dispatch_payload,
    create_approval_stage1_keyboard,
    create_approval_stage2_keyboard,
    create_clio_claim_review_keyboard,
    create_clio_note_suggestion_keyboard,
    render_approval_stage1_text,
    render_approval_stage2_text,
    render_clio_claim_review_text,
    render_clio_note_suggestion_text,
    send_telegram_message,
    send_telegram_text_message,
)

app = FastAPI(title="nanoclaw-llm-proxy", version="2.1.0")
logger = logging.getLogger("nanoclaw.llm_proxy")

METRICS_STORE_PATH = os.getenv("LLM_USAGE_STORE_PATH", "").strip()
SHARED_ROOT = Path((os.getenv("SHARED_ROOT_PATH") or "/app/shared_data").strip() or "/app/shared_data")
LLM_USAGE_FILE = Path(
    (os.getenv("LLM_USAGE_METRICS_PATH") or str(SHARED_ROOT / "logs" / "llm_usage_metrics.json")).strip()
)
OUTBOX_DIR = SHARED_ROOT / "outbox"
LOGS_DIR = SHARED_ROOT / "logs"
UI_LLM_DAILY_LIMIT = int(float(os.getenv("UI_LLM_DAILY_LIMIT", os.getenv("LLM_DAILY_LIMIT", "1000")) or "1000"))

DIRECT_CALLBACK_ACTIONS = {"clio_save", "hermes_deep_dive", "minerva_insight"}
SYSTEM_CALLBACK_ACTIONS = {"clio_confirm_knowledge", "clio_apply_suggestion", "clio_dismiss_suggestion"}
INTERNAL_APPROVAL_ACTIONS = {"approval_yes", "approval_no", "approval_commit"}
TEXT_RATE_WINDOW: dict[str, dict[str, int]] = {}
_CLIO_ALERT_THREAD_STARTED = False


def _read_int_env(name: str, default: int, minimum: int = 0) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(float(raw))
    except ValueError:
        return default
    return max(minimum, parsed)


def _read_bool_env(name: str, fallback: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return fallback
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return fallback


CLIO_ALERT_INTERVAL_SEC = _read_int_env("CLIO_ALERT_SCAN_INTERVAL_SEC", 30, minimum=10)
CLIO_ALERTS_ENABLED = _read_bool_env("CLIO_TELEGRAM_ALERT_ENABLED", True)


def _parse_model_fallbacks(name: str) -> list[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


MODEL_ROUTING = {
    "minerva": os.getenv("MODEL_MINERVA", "gemini-2.5-flash"),
    "clio": os.getenv("MODEL_CLIO", "gemini-2.0-flash-lite"),
    "hermes": os.getenv("MODEL_HERMES", "gemini-2.0-flash"),
}

MODEL_FALLBACKS = {
    "minerva": _parse_model_fallbacks("MODEL_FALLBACK_MINERVA") or ["gemini-2.0-flash-lite"],
    "clio": _parse_model_fallbacks("MODEL_FALLBACK_CLIO") or ["gemini-2.5-flash"],
    "hermes": _parse_model_fallbacks("MODEL_FALLBACK_HERMES") or ["gemini-2.5-flash"],
}

ROLE_BOUNDARY = {
    "minerva": "Orchestrates priorities and decisions. Does not execute external search directly.",
    "clio": "Structures knowledge and documentation only. No trend decision ownership.",
    "hermes": "Collects external signals and writes briefings. No final strategic decision.",
}


def _is_quota_error(exc: RetryableLLMError) -> bool:
    detail = str(exc).lower()
    return "429" in detail or "resource_exhausted" in detail or "quota" in detail


def _model_candidates(agent_id: str) -> list[str]:
    primary = MODEL_ROUTING[agent_id]
    candidates: list[str] = [primary]
    seen = {primary}
    for fallback in MODEL_FALLBACKS[agent_id]:
        if fallback in seen:
            continue
        candidates.append(fallback)
        seen.add(fallback)
    return candidates


def _record_usage(
    *,
    agent_id: str,
    configured_model: str,
    selected_model: str,
    status: str,
    quota_429_hits: int = 0,
    error_detail: str | None = None,
) -> None:
    if not METRICS_STORE_PATH:
        return

    path = Path(METRICS_STORE_PATH)
    now = datetime.now(timezone.utc)
    day_key = now.strftime("%Y-%m-%d")

    try:
        if path.is_file():
            payload = path.read_text(encoding="utf-8")
            data = {} if not payload.strip() else json.loads(payload)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {}

        if not isinstance(data, dict):
            data = {}
        daily = data.setdefault("daily", {})
        if not isinstance(daily, dict):
            daily = {}
            data["daily"] = daily

        entry = daily.setdefault(
            day_key,
            {
                "total": 0,
                "success": 0,
                "transient_error": 0,
                "fatal_error": 0,
                "quota_429": 0,
                "fallback_applied": 0,
                "per_agent": {},
                "per_model": {},
            },
        )
        if not isinstance(entry, dict):
            return

        entry["total"] = int(entry.get("total", 0)) + 1
        if status == "success":
            entry["success"] = int(entry.get("success", 0)) + 1
        elif status == "transient_error":
            entry["transient_error"] = int(entry.get("transient_error", 0)) + 1
        else:
            entry["fatal_error"] = int(entry.get("fatal_error", 0)) + 1

        if quota_429_hits > 0:
            entry["quota_429"] = int(entry.get("quota_429", 0)) + quota_429_hits

        if configured_model != selected_model:
            entry["fallback_applied"] = int(entry.get("fallback_applied", 0)) + 1

        per_agent = entry.setdefault("per_agent", {})
        if isinstance(per_agent, dict):
            per_agent[agent_id] = int(per_agent.get(agent_id, 0)) + 1

        per_model = entry.setdefault("per_model", {})
        if isinstance(per_model, dict):
            per_model[selected_model] = int(per_model.get(selected_model, 0)) + 1

        if error_detail:
            entry["last_error_detail"] = error_detail[:300]

        data["updated_at"] = now.isoformat().replace("+00:00", "Z")
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("usage_metrics_write_failed detail=%s", exc)


def _run_agent_pipeline(
    *,
    agent_id: str,
    message: str,
    history: list[HistoryMessage],
    memory_context: str | None = None,
    source: str = "api",
) -> AgentResponse:
    model_candidates = _model_candidates(agent_id)
    configured_model = model_candidates[0]
    selected_model = configured_model
    reply: str | None = None
    last_retryable: RetryableLLMError | None = None
    quota_429_hits = 0

    for index, model in enumerate(model_candidates):
        selected_model = model
        try:
            reply = generate_agent_reply(
                agent_id=agent_id,
                model=model,
                role_boundary=ROLE_BOUNDARY[agent_id],
                message=message,
                history=history,
                memory_context=memory_context,
            )
            if index > 0:
                logger.warning(
                    "model_fallback_applied agent=%s selected_model=%s primary_model=%s source=%s",
                    agent_id,
                    model,
                    model_candidates[0],
                    source,
                )
            _record_usage(
                agent_id=agent_id,
                configured_model=configured_model,
                selected_model=selected_model,
                status="success",
                quota_429_hits=quota_429_hits,
            )
            break
        except RetryableLLMError as exc:
            last_retryable = exc
            logger.warning("retryable_llm_error agent=%s model=%s detail=%s source=%s", agent_id, model, exc, source)
            if _is_quota_error(exc):
                quota_429_hits += 1
            should_try_fallback = index < len(model_candidates) - 1 and _is_quota_error(exc)
            if should_try_fallback:
                continue
            _record_usage(
                agent_id=agent_id,
                configured_model=configured_model,
                selected_model=selected_model,
                status="transient_error",
                quota_429_hits=quota_429_hits,
                error_detail=str(exc),
            )
            raise HTTPException(status_code=502, detail=f"LLM transient failure: {exc}") from exc
        except FatalLLMError as exc:
            logger.error("fatal_llm_error agent=%s model=%s detail=%s source=%s", agent_id, model, exc, source)
            _record_usage(
                agent_id=agent_id,
                configured_model=configured_model,
                selected_model=selected_model,
                status="fatal_error",
                quota_429_hits=quota_429_hits,
                error_detail=str(exc),
            )
            raise HTTPException(status_code=502, detail=f"LLM fatal failure: {exc}") from exc

    if reply is None:
        detail = f"LLM transient failure: {last_retryable}" if last_retryable else "LLM transient failure"
        _record_usage(
            agent_id=agent_id,
            configured_model=configured_model,
            selected_model=selected_model,
            status="transient_error",
            quota_429_hits=quota_429_hits,
            error_detail=detail,
        )
        raise HTTPException(status_code=502, detail=detail)

    return AgentResponse(
        agent_id=agent_id,
        model=selected_model,
        reply=reply,
        role_boundary=ROLE_BOUNDARY[agent_id],
    )


def _build_minerva_memory_context() -> str | None:
    memory = get_minerva_working_memory()
    return render_minerva_working_memory_context(memory)


def _build_clio_memory_context() -> str | None:
    memory = get_clio_knowledge_memory()
    return render_clio_knowledge_memory_context(memory)


def _build_hermes_memory_context(topic_key: str | None = None) -> str | None:
    memory = get_hermes_evidence_memory()
    return render_hermes_evidence_memory_context(memory, topic_key=topic_key)


def _build_agent_memory_context(agent_id: str, *, topic_key: str | None = None) -> str | None:
    if agent_id == "minerva":
        return _build_minerva_memory_context()
    if agent_id == "clio":
        return _build_clio_memory_context()
    if agent_id == "hermes":
        return _build_hermes_memory_context(topic_key=topic_key)
    return None


def _auto_alert_chat_id() -> str:
    return str(os.getenv("TELEGRAM_CHAT_ID") or "").strip()


def _dispatch_pending_clio_alerts_once() -> None:
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
            _dispatch_pending_clio_alerts_once()
        except Exception:  # noqa: BLE001
            logger.exception("clio alert dispatch loop failed")
        time.sleep(CLIO_ALERT_INTERVAL_SEC)


@app.on_event("startup")
def _start_clio_alert_loop() -> None:
    global _CLIO_ALERT_THREAD_STARTED
    if _CLIO_ALERT_THREAD_STARTED:
        return
    if not CLIO_ALERTS_ENABLED or not _auto_alert_chat_id():
        return
    thread = threading.Thread(target=_clio_alert_loop, name="clio-alert-loop", daemon=True)
    thread.start()
    _CLIO_ALERT_THREAD_STARTED = True


def _normalize_event_input(payload: dict[str, Any]) -> dict[str, Any] | None:
    agent_id = normalize_agent_id(str(payload.get("agentId", "")))
    if not agent_id:
        return None

    topic_key = str(payload.get("topicKey", "")).strip().lower()
    title = str(payload.get("title", "")).strip()
    summary = str(payload.get("summary", "")).strip()
    priority = str(payload.get("priority", "")).strip().lower()
    confidence = float(payload.get("confidence", 0))
    if not topic_key or not title or not summary:
        return None
    if priority not in {"critical", "high", "normal", "low"}:
        return None

    refs = []
    for item in payload.get("sourceRefs") or []:
        if not isinstance(item, dict):
            continue
        source_title = str(item.get("title", "")).strip()
        source_url = str(item.get("url", "")).strip()
        if not source_title or not source_url:
            continue
        tier = str(item.get("priorityTier", "")).strip().upper()
        if tier not in {"P0", "P1", "P2"}:
            tier = ""
        refs.append(
            {
                "title": source_title,
                "url": source_url,
                "snippet": str(item.get("snippet", "")).strip() or None,
                "publisher": str(item.get("publisher", "")).strip() or None,
                "publishedAt": str(item.get("publishedAt", "")).strip() or None,
                "category": str(item.get("category", "")).strip() or None,
                "priorityTier": tier or None,
                "domain": str(item.get("domain", "")).strip() or None,
            }
        )
    source_refs = annotate_source_refs(refs)

    tags = [str(item).strip() for item in (payload.get("tags") or []) if str(item).strip()]
    for source in source_refs:
        category = source.get("category")
        tier = source.get("priorityTier")
        if category:
            tags.append(f"source:{category}")
        if tier:
            tags.append(f"tier:{str(tier).lower()}")

    impact_score = payload.get("impactScore")
    try:
        impact = float(impact_score) if impact_score is not None else None
    except (TypeError, ValueError):
        impact = None
    if impact is not None and not (0 <= impact <= 1):
        impact = None

    return {
        "agentId": agent_id,
        "topicKey": topic_key,
        "title": title,
        "summary": summary,
        "priority": priority,
        "confidence": confidence,
        "tags": list(dict.fromkeys(tags)),
        "sourceRefs": source_refs,
        "impactScore": impact,
        "insightHint": str(payload.get("insightHint", "")).strip() or None,
        "payload": payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
    }


def _resolve_event_theme(payload: dict[str, Any], now: datetime) -> str:
    forced = str(payload.get("forceTheme") or "").strip().lower()
    if forced in {"morning_briefing", "evening_wrapup", "adhoc"}:
        return forced
    return get_journey_theme(now)


def _pick_digest_slot(slots: list[str], theme: str) -> str:
    if not slots:
        return "18:00"
    if theme == "morning_briefing":
        return slots[0]
    if theme == "evening_wrapup":
        return slots[1] if len(slots) > 1 else slots[0]
    return slots[-1]


def _should_auto_save_clio(event: dict[str, Any]) -> dict[str, Any]:
    enabled = _read_bool_env("HERMES_AUTO_CLIO_SAVE", True)
    if not enabled:
        return {"shouldRun": False, "reason": "disabled"}
    if event.get("agentId") != "hermes":
        return {"shouldRun": False, "reason": "agent_not_hermes"}
    if event.get("priority") == "critical":
        return {"shouldRun": True, "reason": "critical_priority"}
    if event.get("priority") != "high":
        return {"shouldRun": False, "reason": "priority_below_high"}

    min_impact = float(os.getenv("HERMES_AUTO_CLIO_SAVE_MIN_IMPACT", "0.75") or 0.75)
    impact_score = float(event.get("impactScore") or 0)
    tags = {str(token).lower() for token in (event.get("tags") or [])}
    has_knowledge_tag = any(tag in tags for tag in {"research", "paper", "analysis", "insight", "whitepaper"})
    if impact_score >= min_impact or has_knowledge_tag:
        return {"shouldRun": True, "reason": "high_impact_or_knowledge_tag"}
    return {"shouldRun": False, "reason": "impact_below_threshold"}


def _parse_allowlist(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {token.strip() for token in raw.split(",") if token.strip()}


def _verify_webhook_secret(request: Request) -> bool:
    expected = (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip()
    if not expected:
        return True
    incoming = (request.headers.get("x-telegram-bot-api-secret-token") or "").strip()
    return bool(incoming) and hmac.compare_digest(incoming, expected)


def _verify_allowlist(update_source: dict[str, Any]) -> tuple[bool, str, str, str]:
    allowed_users = _parse_allowlist(os.getenv("TELEGRAM_ALLOWED_USER_IDS"))
    allowed_chats = _parse_allowlist(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))

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


def _verify_approval_source(approval: dict[str, Any], *, user_id: str, chat_id: str) -> tuple[bool, str]:
    approval_user_id = str(approval.get("requestedByUserId", "")).strip()
    approval_chat_id = str(approval.get("chatId", "")).strip()
    if approval_user_id and user_id and approval_user_id != user_id:
        return False, "approval_user_mismatch"
    if approval_chat_id and chat_id and approval_chat_id != chat_id:
        return False, "approval_chat_mismatch"
    return True, ""


def _is_allowed_action(action: str) -> bool:
    if action in INTERNAL_APPROVAL_ACTIONS:
        return True
    if action in SYSTEM_CALLBACK_ACTIONS:
        return True
    configured = _parse_allowlist(os.getenv("TELEGRAM_ALLOWED_CALLBACK_ACTIONS"))
    if not configured:
        return action in DIRECT_CALLBACK_ACTIONS
    return action in configured


def _approval_queue_enabled() -> bool:
    return _read_bool_env("TELEGRAM_APPROVAL_QUEUE_ENABLED", True)


def _requires_approval(action: str) -> bool:
    if not _approval_queue_enabled():
        return False
    if action in SYSTEM_CALLBACK_ACTIONS:
        return True
    configured = _parse_allowlist(os.getenv("TELEGRAM_APPROVAL_REQUIRED_ACTIONS"))
    if not configured:
        return action in DIRECT_CALLBACK_ACTIONS
    return action in configured


def _check_text_rate_limit(chat_id: str) -> tuple[bool, int]:
    window_sec = _read_int_env("TELEGRAM_TEXT_RATE_LIMIT_WINDOW_SEC", 60, 1)
    max_per_window = _read_int_env("TELEGRAM_TEXT_RATE_LIMIT_MAX", 12, 1)
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


def _compact_line(value: str, max_len: int) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    if len(normalized) <= max_len:
        return normalized
    return f"{normalized[: max_len - 1].rstrip()}…"


def _format_telegram_plain_text(value: str, max_len: int) -> str:
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


def _google_calendar_auto_attach_enabled() -> bool:
    return _read_bool_env("GOOGLE_CALENDAR_ATTACH_TO_MORNING_BRIEFING", True)


def _render_gcal_status_text(status: dict[str, Any]) -> str:
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


def _render_gcal_today_text(today: dict[str, Any], limit: int = 8) -> str:
    events = today.get("events") if isinstance(today, dict) else []
    if not isinstance(events, list):
        events = []
    if not events:
        return "📅 오늘 일정이 없습니다."

    lines = ["📅 오늘 일정 요약"]
    for index, event in enumerate(events[: max(1, limit)], start=1):
        if not isinstance(event, dict):
            continue
        summary = _compact_line(str(event.get("summary") or "(제목 없음)"), 80)
        start = str(event.get("start") or "-")
        end = str(event.get("end") or "-")
        lines.append(f"{index}. {summary} ({start} ~ {end})")
    if len(events) > limit:
        lines.append(f"… 외 {len(events) - limit}건")
    return "\n".join(lines)


def _build_calendar_briefing_payload_for_dispatch() -> dict[str, Any] | None:
    if not is_google_calendar_enabled():
        return None
    if not is_google_calendar_readonly():
        return None
    if not _google_calendar_auto_attach_enabled():
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
                "title": _compact_line(str(item.get("summary") or "(제목 없음)"), 80),
                "timeLabel": _compact_line(time_label, 64),
            }
        )
    return {"summary": f"오늘 일정 {len(events)}건", "items": compact_items}


def _execute_inline_action(action: str, event: dict[str, Any]) -> dict[str, Any]:
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


def _execute_approval_request(approval: dict[str, Any], *, actor_user_id: str) -> dict[str, Any]:
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
    execution = _execute_inline_action(str(approval.get("action", "")), event)
    return {"ok": True, "targetType": "event", **execution}


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "llm-proxy", "version": "2.1.0"}


@app.get("/api/agents")
def list_agents(_: Annotated[None, Depends(verify_internal_request)]) -> dict[str, object]:
    agents = [
        {
            "id": spec.id,
            "display_name": spec.display_name,
            "role": spec.role,
            "model": MODEL_ROUTING[spec.id],
        }
        for spec in AGENT_REGISTRY.values()
    ]
    return {"canonical_ids": list(AGENT_REGISTRY.keys()), "aliases": {}, "agents": agents}


@app.post("/api/agent", response_model=AgentResponse)
def agent_reply(payload: AgentRequest, _: Annotated[None, Depends(verify_internal_request)]) -> AgentResponse:
    normalized = normalize_agent_id(payload.agent_id)
    if not normalized:
        raise HTTPException(status_code=400, detail="Unknown agent id")
    memory_context = payload.memory_context
    if not isinstance(memory_context, str) or not memory_context.strip():
        memory_context = _build_agent_memory_context(normalized)
    return _run_agent_pipeline(
        agent_id=normalized,
        message=payload.message,
        history=payload.history,
        memory_context=memory_context,
        source=payload.source,
    )


@app.post("/api/chat")
def chat_reply(payload: ChatRequest, _: Annotated[None, Depends(verify_internal_request)]) -> dict[str, Any]:
    normalized = normalize_agent_id(payload.agent_id)
    if not normalized:
        raise HTTPException(status_code=400, detail="Unknown agent id")
    memory_context = getattr(payload, "memory_context", None)
    if not isinstance(memory_context, str) or not memory_context.strip():
        memory_context = _build_agent_memory_context(normalized)
    response = _run_agent_pipeline(
        agent_id=normalized,
        message=payload.message,
        history=payload.history,
        memory_context=memory_context,
        source="chat",
    )
    return {"agentId": response.agent_id, "model": response.model, "reply": response.reply}


@app.get("/api/integrations/google-calendar/oauth/start")
def google_calendar_oauth_start(
    _: Annotated[None, Depends(verify_internal_request)],
    return_to: str | None = None,
) -> dict[str, Any]:
    if not is_google_calendar_enabled():
        raise HTTPException(status_code=400, detail="google_calendar_disabled")
    state_record = create_google_oauth_state(return_to=return_to)
    return {
        "ok": True,
        "readonly": is_google_calendar_readonly(),
        "state": state_record.get("state"),
        "authorizationUrl": build_google_oauth_authorization_url(str(state_record.get("state") or "")),
    }


@app.get("/api/integrations/google-calendar/oauth/callback")
def google_calendar_oauth_callback(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    if not is_google_calendar_enabled():
        raise HTTPException(status_code=400, detail="google_calendar_disabled")
    if error:
        raise HTTPException(status_code=400, detail=f"google_oauth_error:{error}")
    if not state or not code:
        raise HTTPException(status_code=400, detail="missing_state_or_code")

    state_record = consume_google_oauth_state(state)
    if not state_record:
        raise HTTPException(status_code=400, detail="invalid_or_expired_state")

    token = save_google_token_from_code(code)

    return_to = str(state_record.get("returnTo") or "")
    if return_to.startswith("telegram:"):
        chat_id = return_to.split(":", 1)[1].strip()
        if chat_id:
            send_telegram_text_message(
                chat_id=chat_id,
                text="✅ Google Calendar read-only 연결이 완료되었습니다.\n`/gcal_today`로 오늘 일정 브리핑을 확인해 보세요.",
            )

    return {
        "ok": True,
        "readonly": is_google_calendar_readonly(),
        "connected": True,
        "tokenUpdatedAt": token.get("updatedAt"),
        "tokenExpiresAt": token.get("expiresAt"),
        "scope": token.get("scope"),
    }


@app.get("/api/integrations/google-calendar/status")
def google_calendar_status(_: Annotated[None, Depends(verify_internal_request)]) -> dict[str, Any]:
    return {"ok": True, **get_google_calendar_connection_status()}


@app.get("/api/integrations/google-calendar/today")
def google_calendar_today(_: Annotated[None, Depends(verify_internal_request)]) -> dict[str, Any]:
    if not is_google_calendar_enabled():
        raise HTTPException(status_code=400, detail="google_calendar_disabled")
    return {"ok": True, **list_google_today_events()}


@app.post("/api/search", response_model=SearchResponse)
def search_data(
    payload: SearchRequest,
    _: Annotated[None, Depends(verify_internal_request)],
) -> SearchResponse:
    try:
        sanitized_results, provider, filter_stats = get_search_results(query=payload.query, max_results=payload.max_results)
    except SearchProviderError as exc:
        raise HTTPException(status_code=502, detail=f"search provider failure: {exc}") from exc
    return SearchResponse(query=payload.query, results=sanitized_results, provider=provider, filter_stats=filter_stats)


@app.post("/api/orchestration/events")
async def orchestration_events(
    request: Request,
    _: Annotated[None, Depends(verify_internal_request)],
) -> JSONResponse:
    raw_body = await request.json()
    contract = validate_event_contract_v1(
        raw_body,
        require_explicit_schema_version=_read_bool_env("ORCH_REQUIRE_SCHEMA_V1", False),
    )
    if not contract.get("ok"):
        return JSONResponse(
            {
                "error": contract.get("error"),
                "schemaVersion": contract.get("schemaVersion"),
                "mode": contract.get("mode"),
                "required": contract.get("required"),
                "issues": contract.get("issues"),
            },
            status_code=400,
        )

    payload = dict(contract.get("payload") or {})
    normalized = _normalize_event_input(payload)
    if not normalized:
        return JSONResponse(
            {
                "error": "invalid_event_payload_after_contract_validation",
                "schemaVersion": ORCHESTRATION_EVENT_SCHEMA_VERSION,
                "required": ["agentId", "topicKey", "title", "summary", "priority", "confidence"],
            },
            status_code=400,
        )

    now = datetime.now(timezone.utc)
    theme = _resolve_event_theme(payload, now)
    event = {
        **normalized,
        "eventId": create_event_id(),
        "createdAt": now.isoformat().replace("+00:00", "Z"),
        "theme": theme,
        "dedupeKey": make_dedupe_key(normalized["topicKey"], normalized["summary"]),
    }

    policy = get_dispatch_policy()
    cooldown_until = get_cooldown(normalized["topicKey"])
    if payload.get("forceDispatch"):
        outcome = {"decision": "send_now", "reason": "force_dispatch", "mode": "immediate"}
    else:
        outcome = evaluate_dispatch_policy(
            priority=normalized["priority"],
            confidence=float(normalized["confidence"]),
            policy=policy,
            cooldown_until=cooldown_until,
            now=now,
        )

    auto_clio_policy = _should_auto_save_clio(event)
    auto_clio: dict[str, Any] = {"created": False, "reason": auto_clio_policy["reason"]}
    if auto_clio_policy["shouldRun"]:
        try:
            task = create_inbox_task(
                target_agent_id="clio",
                reason="hermes_high_impact_auto_clio_save",
                topic_key=event["topicKey"],
                title=event["title"],
                summary=event["summary"],
                source_refs=[{"title": str(item.get("title", "")), "url": str(item.get("url", ""))} for item in event["sourceRefs"]],
            )
            auto_clio = {"created": True, "reason": auto_clio_policy["reason"], "inboxFile": task["inboxFile"], "path": task["path"]}
        except Exception as exc:  # noqa: BLE001
            auto_clio = {"created": False, "reason": "create_inbox_failed", "error": str(exc)}

    if outcome.get("decision") in {"queue_digest", "suppressed_cooldown"}:
        slot = _pick_digest_slot(list(policy.get("digestSlots", ["18:00"])), event["theme"])
        push_digest_item(slot, event)

    telegram = {"sent": False, "reason": "not_attempted"}
    calendar_briefing: dict[str, Any] | None = None
    if event.get("theme") == "morning_briefing":
        calendar_briefing = _build_calendar_briefing_payload_for_dispatch()

    chat_id = str(payload.get("chatId") or os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if outcome.get("decision") == "send_now" and chat_id:
        dispatch_payload = build_telegram_dispatch_payload(chat_id=chat_id, event=event, calendar_briefing=calendar_briefing)
        send_result = send_telegram_message(dispatch_payload)
        telegram = {"sent": bool(send_result.get("sent")), "reason": "ok" if send_result.get("sent") else send_result.get("reason")}

    append_agent_event(
        {
            **event,
            "payload": {
                **(event.get("payload") or {}),
                "orchestration": {
                    "schemaVersion": ORCHESTRATION_EVENT_SCHEMA_VERSION,
                    "contractMode": contract.get("mode"),
                    "decision": outcome.get("decision"),
                    "reason": outcome.get("reason"),
                    "mode": outcome.get("mode"),
                    "cooldownUntil": outcome.get("cooldownUntil"),
                    "telegram": telegram,
                    "autoClio": {"created": auto_clio.get("created"), "reason": auto_clio.get("reason")},
                },
            },
        }
    )

    return JSONResponse(
        {
            "ok": True,
            "schemaVersion": ORCHESTRATION_EVENT_SCHEMA_VERSION,
            "contractMode": contract.get("mode"),
            "eventId": event["eventId"],
            "theme": event["theme"],
            "decision": outcome.get("decision"),
            "reason": outcome.get("reason"),
            "mode": outcome.get("mode"),
            "policy": policy,
            "cooldownUntil": outcome.get("cooldownUntil"),
            "telegram": telegram,
            "calendarBriefingAttached": bool(calendar_briefing is not None),
            "autoClio": auto_clio,
        }
    )


@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request) -> JSONResponse:
    if not _verify_webhook_secret(request):
        return JSONResponse({"error": "unauthorized_webhook"}, status_code=401)

    update = await request.json()
    callback = update.get("callback_query") if isinstance(update, dict) else None
    message = update.get("message") if isinstance(update, dict) else None

    if isinstance(callback, dict):
        callback_id = str(callback.get("id") or "").strip()
        callback_data = str(callback.get("data") or "").strip()
        if not callback_id or not callback_data:
            return JSONResponse({"ok": True, "ignored": True, "reason": "no_callback_query"})

        tokens = [item.strip() for item in callback_data.split(":") if item.strip()]
        if len(tokens) < 2:
            answer_telegram_callback(callback_query_id=callback_id, text="지원하지 않는 액션입니다.")
            return JSONResponse({"ok": True, "ignored": True, "reason": "invalid_callback_data"})

        action, args = tokens[0], tokens[1:]
        if not _is_allowed_action(action):
            answer_telegram_callback(callback_query_id=callback_id, text="허용되지 않은 액션입니다.", show_alert=True)
            return JSONResponse({"ok": True, "ignored": True, "reason": "action_not_allowed"})

        source = {
            "userId": (callback.get("from") or {}).get("id") if isinstance(callback.get("from"), dict) else "",
            "chatId": ((callback.get("message") or {}).get("chat") or {}).get("id")
            if isinstance((callback.get("message") or {}).get("chat"), dict)
            else "",
        }
        allow_ok, allow_reason, user_id, chat_id = _verify_allowlist(source)
        if not allow_ok:
            answer_telegram_callback(callback_query_id=callback_id, text="권한이 없는 요청입니다.", show_alert=True)
            return JSONResponse({"error": "forbidden_callback_source", "reason": allow_reason}, status_code=403)

        if action == "approval_no":
            approval_id = args[0] if args else ""
            if not approval_id:
                answer_telegram_callback(callback_query_id=callback_id, text="승인 ID가 없습니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "approval_id_missing"})
            current = get_approval_request(approval_id)
            if not current:
                answer_telegram_callback(callback_query_id=callback_id, text="승인 요청을 찾지 못했습니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "approval_not_found"})
            source_ok, source_reason = _verify_approval_source(current, user_id=user_id, chat_id=chat_id)
            if not source_ok:
                answer_telegram_callback(callback_query_id=callback_id, text="다른 요청자의 승인 항목입니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": source_reason, "approval": current})
            approval = reject_approval_request(approval_id, user_id)
            if not approval:
                answer_telegram_callback(callback_query_id=callback_id, text="승인 요청을 찾지 못했습니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "approval_not_found_after_verify"})
            answer_telegram_callback(callback_query_id=callback_id, text="승인 요청을 취소했습니다.")
            return JSONResponse({"ok": True, "mode": "callback_query", "action": action, "approval": approval})

        if action == "approval_yes":
            approval_id = args[0] if args else ""
            if not approval_id:
                answer_telegram_callback(callback_query_id=callback_id, text="승인 ID가 없습니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "approval_id_missing"})
            current = get_approval_request(approval_id)
            if not current:
                answer_telegram_callback(callback_query_id=callback_id, text="승인 요청을 찾지 못했습니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "approval_not_found"})
            source_ok, source_reason = _verify_approval_source(current, user_id=user_id, chat_id=chat_id)
            if not source_ok:
                answer_telegram_callback(callback_query_id=callback_id, text="다른 요청자의 승인 항목입니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": source_reason, "approval": current})
            if current.get("status") == "expired":
                answer_telegram_callback(callback_query_id=callback_id, text="승인 요청이 만료되었습니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "approval_expired", "approval": current})
            if current.get("status") != "pending_stage1":
                answer_telegram_callback(callback_query_id=callback_id, text="이미 처리된 승인 요청입니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "approval_not_pending_stage1", "approval": current})

            stage_one = approve_stage_one(approval_id, user_id)
            if not stage_one:
                answer_telegram_callback(callback_query_id=callback_id, text="승인 요청을 찾지 못했습니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "approval_not_found_after_stage1"})

            if int(stage_one.get("requiredSteps", 2)) == 1:
                execution = _execute_approval_request(stage_one, actor_user_id=user_id)
                if not execution.get("ok"):
                    answer_telegram_callback(callback_query_id=callback_id, text="승인 실행 대상이 유효하지 않습니다.", show_alert=True)
                    return JSONResponse(
                        {
                            "ok": True,
                            "ignored": True,
                            "reason": str(execution.get("reason") or "approval_execution_failed"),
                            "approval": stage_one,
                        }
                    )
                executed = mark_approval_executed(str(stage_one.get("id", "")), user_id)
                answer_telegram_callback(callback_query_id=callback_id, text=str(execution.get("callbackText", "")))
                return JSONResponse(
                    {
                        "ok": True,
                        "mode": "callback_query",
                        "action": stage_one.get("action"),
                        "eventId": stage_one.get("eventId"),
                        "inbox": execution.get("inbox"),
                        "claimReview": execution.get("claimReview"),
                        "approval": executed or stage_one,
                    }
                )

            send_telegram_message(
                {
                    "chat_id": chat_id,
                    "text": render_approval_stage2_text(stage_one),
                    "reply_markup": create_approval_stage2_keyboard(str(stage_one.get("id", ""))),
                    "disable_web_page_preview": True,
                }
            )
            answer_telegram_callback(callback_query_id=callback_id, text="1차 확인 완료. 최종 승인을 진행하세요.")
            return JSONResponse({"ok": True, "mode": "callback_query", "action": action, "approval": stage_one})

        if action == "approval_commit":
            approval_id = args[0] if args else ""
            if not approval_id:
                answer_telegram_callback(callback_query_id=callback_id, text="승인 ID가 없습니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "approval_id_missing"})
            approval = get_approval_request(approval_id)
            if not approval:
                answer_telegram_callback(callback_query_id=callback_id, text="승인 요청을 찾지 못했습니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "approval_not_found"})
            source_ok, source_reason = _verify_approval_source(approval, user_id=user_id, chat_id=chat_id)
            if not source_ok:
                answer_telegram_callback(callback_query_id=callback_id, text="다른 요청자의 승인 항목입니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": source_reason, "approval": approval})
            if approval.get("status") == "expired":
                answer_telegram_callback(callback_query_id=callback_id, text="승인 요청이 만료되었습니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "approval_expired", "approval": approval})
            if approval.get("status") != "pending_stage2":
                answer_telegram_callback(callback_query_id=callback_id, text="최종 승인 가능한 상태가 아닙니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "approval_not_pending_stage2", "approval": approval})
            execution = _execute_approval_request(approval, actor_user_id=user_id)
            if not execution.get("ok"):
                answer_telegram_callback(callback_query_id=callback_id, text="승인 실행 대상이 유효하지 않습니다.", show_alert=True)
                return JSONResponse(
                    {
                        "ok": True,
                        "ignored": True,
                        "reason": str(execution.get("reason") or "approval_execution_failed"),
                        "approval": approval,
                    }
                )
            executed = mark_approval_executed(str(approval.get("id", "")), user_id)
            answer_telegram_callback(callback_query_id=callback_id, text=str(execution.get("callbackText", "")))
            return JSONResponse(
                {
                    "ok": True,
                    "mode": "callback_query",
                    "action": approval.get("action"),
                    "eventId": approval.get("eventId"),
                    "inbox": execution.get("inbox"),
                    "claimReview": execution.get("claimReview"),
                    "approval": executed or approval,
                }
            )

        if action == "clio_confirm_knowledge":
            review_id = args[0] if args else ""
            if not review_id:
                answer_telegram_callback(callback_query_id=callback_id, text="검토 ID가 없습니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "claim_review_id_missing"})
            review = get_clio_claim_review(review_id)
            if not review:
                answer_telegram_callback(callback_query_id=callback_id, text="Clio 검토 항목을 찾지 못했습니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "claim_review_not_found"})
            if str(review.get("status") or "") != "pending_user_review":
                answer_telegram_callback(callback_query_id=callback_id, text="이미 처리된 Clio 검토 항목입니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "claim_review_not_pending", "claimReview": review})
            created = create_approval_request(
                action=action,
                event_id=f"clio-claim-review:{review_id}",
                event_title=str(review.get("title", "")),
                topic_key=str(review.get("topicKey", "")),
                chat_id=chat_id,
                requested_by_user_id=user_id,
                payload={
                    "targetType": "clio_claim_review",
                    "reviewId": review_id,
                    "vaultFile": str(review.get("vaultFile", "")),
                },
            )
            approval = created["approval"]
            send_telegram_message(
                {
                    "chat_id": chat_id,
                    "text": render_approval_stage1_text(approval),
                    "reply_markup": create_approval_stage1_keyboard(str(approval.get("id", ""))),
                    "disable_web_page_preview": True,
                }
            )
            answer_telegram_callback(
                callback_query_id=callback_id,
                text="이미 생성된 승인 요청이 있습니다." if created.get("reused") else "Clio 지식 노트 승인 요청을 생성했습니다.",
            )
            return JSONResponse(
                {
                    "ok": True,
                    "mode": "callback_query",
                    "action": action,
                    "approvalRequired": True,
                    "approval": approval,
                    "claimReview": review,
                    "reused": bool(created.get("reused")),
                }
            )

        if action == "clio_dismiss_suggestion":
            suggestion_id = args[0] if args else ""
            if not suggestion_id:
                answer_telegram_callback(callback_query_id=callback_id, text="제안 ID가 없습니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "clio_note_suggestion_id_missing"})
            suggestion = get_clio_note_suggestion(suggestion_id)
            if not suggestion:
                answer_telegram_callback(callback_query_id=callback_id, text="Clio note suggestion을 찾지 못했습니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "clio_note_suggestion_not_found"})
            if str(suggestion.get("suggestionState") or "") != "pending":
                answer_telegram_callback(callback_query_id=callback_id, text="이미 처리된 Clio note suggestion입니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "clio_note_suggestion_not_pending", "suggestion": suggestion})
            dismissed = dismiss_clio_note_suggestion(suggestion_id, user_id)
            answer_telegram_callback(callback_query_id=callback_id, text="Clio note suggestion을 보류했습니다.")
            return JSONResponse(
                {
                    "ok": True,
                    "mode": "callback_query",
                    "action": action,
                    "suggestion": dismissed or suggestion,
                }
            )

        if action == "clio_apply_suggestion":
            suggestion_id = args[0] if args else ""
            if not suggestion_id:
                answer_telegram_callback(callback_query_id=callback_id, text="제안 ID가 없습니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "clio_note_suggestion_id_missing"})
            suggestion = get_clio_note_suggestion(suggestion_id)
            if not suggestion:
                answer_telegram_callback(callback_query_id=callback_id, text="Clio note suggestion을 찾지 못했습니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "clio_note_suggestion_not_found"})
            if str(suggestion.get("suggestionState") or "") != "pending":
                answer_telegram_callback(callback_query_id=callback_id, text="이미 처리된 Clio note suggestion입니다.", show_alert=True)
                return JSONResponse({"ok": True, "ignored": True, "reason": "clio_note_suggestion_not_pending", "suggestion": suggestion})
            created = create_approval_request(
                action=action,
                event_id=f"clio-note-suggestion:{suggestion_id}",
                event_title=str(suggestion.get("title", "")),
                topic_key=str(suggestion.get("title", "")),
                chat_id=chat_id,
                requested_by_user_id=user_id,
                payload={
                    "targetType": "clio_note_suggestion",
                    "suggestionId": suggestion_id,
                    "vaultFile": str(suggestion.get("vaultFile", "")),
                },
            )
            approval = created["approval"]
            send_telegram_message(
                {
                    "chat_id": chat_id,
                    "text": render_approval_stage1_text(approval),
                    "reply_markup": create_approval_stage1_keyboard(str(approval.get("id", ""))),
                    "disable_web_page_preview": True,
                }
            )
            answer_telegram_callback(
                callback_query_id=callback_id,
                text="이미 생성된 승인 요청이 있습니다." if created.get("reused") else "Clio note suggestion 승인 요청을 생성했습니다.",
            )
            return JSONResponse(
                {
                    "ok": True,
                    "mode": "callback_query",
                    "action": action,
                    "approvalRequired": True,
                    "approval": approval,
                    "suggestion": suggestion,
                    "reused": bool(created.get("reused")),
                }
            )

        if action not in DIRECT_CALLBACK_ACTIONS:
            answer_telegram_callback(callback_query_id=callback_id, text="지원하지 않는 액션입니다.")
            return JSONResponse({"ok": True, "ignored": True, "reason": "unsupported_action"})

        event_id = args[0] if args else ""
        if not event_id:
            answer_telegram_callback(callback_query_id=callback_id, text="원본 이벤트 ID가 없습니다.", show_alert=True)
            return JSONResponse({"ok": True, "ignored": True, "reason": "event_id_missing"})
        event = find_event_by_id(event_id)
        if not event:
            answer_telegram_callback(callback_query_id=callback_id, text="원본 이벤트를 찾을 수 없습니다.")
            return JSONResponse({"ok": True, "ignored": True, "reason": "event_not_found"})

        if _requires_approval(action):
            created = create_approval_request(
                action=action,
                event_id=str(event.get("eventId", "")),
                event_title=str(event.get("title", "")),
                topic_key=str(event.get("topicKey", "")),
                chat_id=chat_id,
                requested_by_user_id=user_id,
            )
            approval = created["approval"]
            send_telegram_message(
                {
                    "chat_id": chat_id,
                    "text": render_approval_stage1_text(approval),
                    "reply_markup": create_approval_stage1_keyboard(str(approval.get("id", ""))),
                    "disable_web_page_preview": True,
                }
            )
            answer_telegram_callback(
                callback_query_id=callback_id,
                text="이미 생성된 승인 요청이 있습니다." if created.get("reused") else "승인 요청을 생성했습니다.",
            )
            return JSONResponse(
                {
                    "ok": True,
                    "mode": "callback_query",
                    "action": action,
                    "eventId": event.get("eventId"),
                    "approvalRequired": True,
                    "approval": approval,
                    "reused": bool(created.get("reused")),
                }
            )

        execution = _execute_inline_action(action, event)
        answer_telegram_callback(callback_query_id=callback_id, text=str(execution.get("callbackText", "")))
        return JSONResponse(
            {
                "ok": True,
                "mode": "callback_query",
                "action": action,
                "eventId": event.get("eventId"),
                "inbox": execution.get("inbox"),
            }
        )

    if isinstance(message, dict) and isinstance(message.get("text"), str):
        text = str(message.get("text", "")).strip()
        if not text:
            return JSONResponse({"ok": True, "ignored": True, "reason": "empty_message_text"})
        source = {
            "userId": (message.get("from") or {}).get("id") if isinstance(message.get("from"), dict) else "",
            "chatId": (message.get("chat") or {}).get("id") if isinstance(message.get("chat"), dict) else "",
        }
        allow_ok, allow_reason, user_id, chat_id = _verify_allowlist(source)
        if not allow_ok:
            return JSONResponse({"error": "forbidden_message_source", "reason": allow_reason}, status_code=403)

        if text in {"/start", "/help"}:
            help_text = (
                "🤝 Minerva 대화 모드입니다.\n\n"
                "• 일반 메시지를 보내면 Minerva가 답변합니다.\n"
                "• /reset 으로 대화 히스토리를 초기화할 수 있습니다.\n"
                "• /clio_reviews : 승인 대기 중인 Clio knowledge 노트 확인\n"
                "• /clio_suggestions : 기존 노트 update/merge 제안 확인\n"
                "• /gcal_connect : Google Calendar read-only 연결 링크 발급\n"
                "• /gcal_status : Calendar 연결 상태 확인\n"
                "• /gcal_today : 오늘 일정 요약 확인\n"
                "• 인라인 버튼은 브리핑 메시지 하단에서 사용할 수 있습니다."
            )
            send_result = send_telegram_text_message(chat_id=chat_id, text=help_text)
            return JSONResponse({"ok": True, "mode": "message_text", "command": text, "chatId": chat_id, "telegram": send_result})

        if text == "/reset":
            clear_telegram_chat_history(chat_id)
            send_result = send_telegram_text_message(chat_id=chat_id, text="🧹 Minerva 대화 컨텍스트를 초기화했습니다.")
            return JSONResponse({"ok": True, "mode": "message_text", "command": text, "chatId": chat_id, "telegram": send_result})

        if text == "/clio_reviews":
            reviews = list_pending_clio_claim_reviews(limit=5)
            if not reviews:
                send_result = send_telegram_text_message(chat_id=chat_id, text="현재 승인 대기 중인 Clio knowledge 노트가 없습니다.")
                return JSONResponse(
                    {
                        "ok": True,
                        "mode": "message_text",
                        "command": text,
                        "chatId": chat_id,
                        "pendingCount": 0,
                        "telegram": send_result,
                    }
                )
            review = reviews[0]
            send_result = send_telegram_message(
                {
                    "chat_id": chat_id,
                    "text": render_clio_claim_review_text(review, pending_count=len(reviews)),
                    "reply_markup": create_clio_claim_review_keyboard(str(review.get("id", ""))),
                    "disable_web_page_preview": True,
                }
            )
            return JSONResponse(
                {
                    "ok": True,
                    "mode": "message_text",
                    "command": text,
                    "chatId": chat_id,
                    "pendingCount": len(reviews),
                    "review": review,
                    "telegram": send_result,
                }
            )

        if text == "/clio_suggestions":
            suggestions = list_pending_clio_note_suggestions(limit=5)
            if not suggestions:
                send_result = send_telegram_text_message(chat_id=chat_id, text="현재 승인 대기 중인 Clio note suggestion이 없습니다.")
                return JSONResponse(
                    {
                        "ok": True,
                        "mode": "message_text",
                        "command": text,
                        "chatId": chat_id,
                        "pendingCount": 0,
                        "telegram": send_result,
                    }
                )
            suggestion = suggestions[0]
            send_result = send_telegram_message(
                {
                    "chat_id": chat_id,
                    "text": render_clio_note_suggestion_text(suggestion, pending_count=len(suggestions)),
                    "reply_markup": create_clio_note_suggestion_keyboard(str(suggestion.get("id", ""))),
                    "disable_web_page_preview": True,
                }
            )
            return JSONResponse(
                {
                    "ok": True,
                    "mode": "message_text",
                    "command": text,
                    "chatId": chat_id,
                    "pendingCount": len(suggestions),
                    "suggestion": suggestion,
                    "telegram": send_result,
                }
            )

        if text == "/gcal_connect":
            if not is_google_calendar_enabled():
                send_result = send_telegram_text_message(chat_id=chat_id, text="Google Calendar 연동이 비활성화되어 있습니다.")
                return JSONResponse({"ok": True, "mode": "message_text", "command": text, "chatId": chat_id, "telegram": send_result})
            try:
                state_record = create_google_oauth_state(return_to=f"telegram:{chat_id}")
                authorization_url = build_google_oauth_authorization_url(str(state_record.get("state") or ""))
                connect_text = (
                    "📅 Google Calendar read-only 연결\n"
                    "아래 링크를 열어 OAuth 동의를 완료해 주세요.\n"
                    f"{authorization_url}"
                )
                send_result = send_telegram_text_message(chat_id=chat_id, text=connect_text)
                return JSONResponse(
                    {
                        "ok": True,
                        "mode": "message_text",
                        "command": text,
                        "chatId": chat_id,
                        "authorizationUrl": authorization_url,
                        "telegram": send_result,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                send_result = send_telegram_text_message(chat_id=chat_id, text=f"Calendar 연결 링크 생성 실패: {exc}")
                return JSONResponse(
                    {
                        "ok": False,
                        "mode": "message_text",
                        "command": text,
                        "chatId": chat_id,
                        "error": str(exc),
                        "telegram": send_result,
                    },
                    status_code=500,
                )

        if text == "/gcal_status":
            status = get_google_calendar_connection_status()
            status_text = _render_gcal_status_text(status)
            send_result = send_telegram_text_message(chat_id=chat_id, text=status_text)
            return JSONResponse(
                {
                    "ok": True,
                    "mode": "message_text",
                    "command": text,
                    "chatId": chat_id,
                    "status": status,
                    "telegram": send_result,
                }
            )

        if text == "/gcal_today":
            if not is_google_calendar_enabled():
                send_result = send_telegram_text_message(chat_id=chat_id, text="Google Calendar 연동이 비활성화되어 있습니다.")
                return JSONResponse({"ok": True, "mode": "message_text", "command": text, "chatId": chat_id, "telegram": send_result})
            try:
                today = list_google_today_events()
                today_text = _render_gcal_today_text(today)
                send_result = send_telegram_text_message(chat_id=chat_id, text=today_text)
                return JSONResponse(
                    {
                        "ok": True,
                        "mode": "message_text",
                        "command": text,
                        "chatId": chat_id,
                        "today": today,
                        "telegram": send_result,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                send_result = send_telegram_text_message(chat_id=chat_id, text=f"오늘 일정 조회 실패: {exc}")
                return JSONResponse(
                    {
                        "ok": False,
                        "mode": "message_text",
                        "command": text,
                        "chatId": chat_id,
                        "error": str(exc),
                        "telegram": send_result,
                    },
                    status_code=500,
                )

        rate_ok, retry_after = _check_text_rate_limit(chat_id)
        if not rate_ok:
            send_result = send_telegram_text_message(
                chat_id=chat_id,
                text=f"요청이 많아 잠시 제한합니다. {retry_after}초 후 다시 시도해 주세요.",
            )
            return JSONResponse(
                {
                    "ok": False,
                    "mode": "message_text",
                    "error": "rate_limited",
                    "retryAfterSec": retry_after,
                    "telegram": send_result,
                },
                status_code=429,
            )

        history_limit = _read_int_env("TELEGRAM_MINERVA_HISTORY_TURNS", 10, 1)
        max_history_entries = max(4, history_limit * 2)
        history_rows = get_telegram_chat_history(chat_id, max_history_entries)
        history = [
            HistoryMessage(role=str(entry.get("role", "")), text=str(entry.get("text", "")), at=str(entry.get("at", "")))
            for entry in history_rows
        ]

        minerva_error: str | None = None
        model: str | None = None
        try:
            result = _run_agent_pipeline(
                agent_id="minerva",
                message=text,
                history=history,
                memory_context=_build_minerva_memory_context(),
                source="telegram",
            )
            reply = _format_telegram_plain_text(result.reply, 3200)
            model = result.model
            if not reply:
                raise RuntimeError("empty_reply")
        except Exception as exc:  # noqa: BLE001
            minerva_error = str(exc)
            reply = "현재 Minerva 응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요."

        append_telegram_chat_history(
            chat_id=chat_id,
            user_text=_compact_line(text, 1200),
            assistant_text=_compact_line(reply, 2400),
            max_entries=max_history_entries,
        )
        send_result = send_telegram_text_message(chat_id=chat_id, text=reply)
        return JSONResponse(
            {
                "ok": True,
                "mode": "message_text",
                "chatId": chat_id,
                "userId": user_id,
                "model": model,
                "minerva": {"ok": minerva_error is None, "error": minerva_error},
                "telegram": send_result,
            }
        )

    return JSONResponse({"ok": True, "ignored": True, "reason": "unsupported_update_type"})


def _read_json_file(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return fallback


@app.get("/api/runtime-metrics")
def runtime_metrics(_: Annotated[None, Depends(verify_internal_request)]) -> dict[str, Any]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage_payload = _read_json_file(LLM_USAGE_FILE, {})
    daily = usage_payload.get("daily", {}) if isinstance(usage_payload, dict) else {}
    entry = daily.get(today, {}) if isinstance(daily, dict) else {}
    if not isinstance(entry, dict):
        entry = {}

    total = int(entry.get("total", 0) or 0)
    success = int(entry.get("success", 0) or 0)
    transient_error = int(entry.get("transient_error", 0) or 0)
    fatal_error = int(entry.get("fatal_error", 0) or 0)
    quota_429 = int(entry.get("quota_429", 0) or 0)
    fallback_applied = int(entry.get("fallback_applied", 0) or 0)
    daily_limit = UI_LLM_DAILY_LIMIT if UI_LLM_DAILY_LIMIT > 0 else 1000

    events = list_agent_events()
    by_priority = {"critical": 0, "high": 0, "normal": 0, "low": 0}
    by_theme = {"morning_briefing": 0, "evening_wrapup": 0, "adhoc": 0}
    by_decision = {"send_now": 0, "queue_digest": 0, "suppressed_cooldown": 0, "unknown": 0}
    telegram_attempted = 0
    telegram_sent = 0
    auto_clio_attempted = 0
    auto_clio_created = 0

    for event in events:
        priority = str(event.get("priority", "normal"))
        by_priority[priority] = by_priority.get(priority, 0) + 1
        theme = str(event.get("theme", "adhoc"))
        by_theme[theme] = by_theme.get(theme, 0) + 1

        payload = event.get("payload", {})
        orchestration = payload.get("orchestration", {}) if isinstance(payload, dict) else {}
        if not isinstance(orchestration, dict):
            by_decision["unknown"] += 1
            continue
        decision = str(orchestration.get("decision", "unknown"))
        by_decision[decision] = by_decision.get(decision, 0) + 1

        telegram = orchestration.get("telegram", {})
        if isinstance(telegram, dict):
            telegram_attempted += 1
            if telegram.get("sent") is True:
                telegram_sent += 1

        auto_clio = orchestration.get("autoClio", {})
        if isinstance(auto_clio, dict):
            auto_clio_attempted += 1
            if auto_clio.get("created") is True:
                auto_clio_created += 1

    approval_stats = get_approval_queue_stats()
    pending_clio_claim_reviews = len(list_pending_clio_claim_reviews(limit=200))
    pending_clio_note_suggestions = len(list_pending_clio_note_suggestions(limit=200))

    required = 0
    translated = 0
    failed = 0
    try:
        files = [file for file in OUTBOX_DIR.iterdir() if file.name.endswith(".json")]
    except Exception:  # noqa: BLE001
        files = []
    for file in files[-500:]:
        payload = _read_json_file(file, {})
        deepl_required = payload.get("deepl_required") is True if isinstance(payload, dict) else False
        deepl_applied = payload.get("deepl_applied") is True if isinstance(payload, dict) else False
        if not deepl_required:
            continue
        required += 1
        if deepl_applied:
            translated += 1
        else:
            failed += 1

    latest_report = None
    fail_count = 0
    warn_count = 0
    security_fail_count = 0
    try:
        reports = sorted(
            [file.name for file in LOGS_DIR.iterdir() if file.name.startswith("daily-verify-") and file.name.endswith(".log")],
            reverse=True,
        )
        if reports:
            latest_report = reports[0]
            log_raw = (LOGS_DIR / latest_report).read_text(encoding="utf-8")
            for line in log_raw.splitlines():
                if "FAIL" in line:
                    fail_count += 1
                if "WARN" in line:
                    warn_count += 1
                if "[security-orch] FAIL" in line:
                    security_fail_count += 1
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "llm": {
            "total": total,
            "success": success,
            "transientError": transient_error,
            "fatalError": fatal_error,
            "quota429": quota_429,
            "fallbackApplied": fallback_applied,
            "successRate": _ratio(success, total),
            "dailyLimit": daily_limit,
            "remaining": max(0, daily_limit - total),
            "latencyMs": {"p95": None, "note": "latency histogram not yet persisted"},
            "perAgent": entry.get("per_agent", {}),
            "perModel": entry.get("per_model", {}),
            "updatedAt": usage_payload.get("updated_at") if isinstance(usage_payload, dict) else None,
        },
        "orchestration": {
            "totalEvents": len(events),
            "byPriority": by_priority,
            "byTheme": by_theme,
            "byDecision": by_decision,
            "telegram": {
                "attempted": telegram_attempted,
                "sent": telegram_sent,
                "successRate": _ratio(telegram_sent, telegram_attempted),
            },
            "autoClio": {
                "attempted": auto_clio_attempted,
                "created": auto_clio_created,
                "successRate": _ratio(auto_clio_created, auto_clio_attempted),
            },
            "pendingClioClaimReviews": pending_clio_claim_reviews,
            "pendingClioNoteSuggestions": pending_clio_note_suggestions,
            "pendingApprovals": approval_stats.get("pending", 0),
            "approvalQueue": approval_stats,
        },
        "deepl": {
            "source": "clio_outbox",
            "required": required,
            "translated": translated,
            "failed": failed,
            "successRate": _ratio(translated, required),
        },
        "security": {
            "openIssues": fail_count,
            "securityIssues": security_fail_count,
            "warnings": warn_count,
            "latestReport": latest_report,
        },
    }
