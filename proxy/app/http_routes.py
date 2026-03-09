from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

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
from .models import AgentRequest, AgentResponse, ChatRequest, SearchRequest, SearchResponse
from .orch_store import (
    get_approval_queue_stats,
    list_agent_events,
    list_pending_clio_claim_reviews,
    list_pending_clio_note_suggestions,
)
from .role_runtime import (
    MODEL_ROUTING,
    build_agent_memory_context,
    run_agent_pipeline,
)
from .search_client import SearchProviderError, get_search_results
from .security import verify_internal_request
from .telegram_bridge import send_telegram_text_message

router = APIRouter()

SHARED_ROOT = Path((os.getenv("SHARED_ROOT_PATH") or "/app/shared_data").strip() or "/app/shared_data")
LLM_USAGE_FILE = Path(
    (os.getenv("LLM_USAGE_METRICS_PATH") or str(SHARED_ROOT / "logs" / "llm_usage_metrics.json")).strip()
)
OUTBOX_DIR = SHARED_ROOT / "outbox"
LOGS_DIR = SHARED_ROOT / "logs"
UI_LLM_DAILY_LIMIT = int(float(os.getenv("UI_LLM_DAILY_LIMIT", os.getenv("LLM_DAILY_LIMIT", "1000")) or "1000"))


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _read_json_file(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return fallback


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "llm-proxy", "version": "2.1.0"}


@router.get("/api/agents")
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


@router.post("/api/agent", response_model=AgentResponse)
def agent_reply(payload: AgentRequest, _: Annotated[None, Depends(verify_internal_request)]) -> AgentResponse:
    normalized = normalize_agent_id(payload.agent_id)
    if not normalized:
        raise HTTPException(status_code=400, detail="Unknown agent id")
    memory_context = payload.memory_context
    if not isinstance(memory_context, str) or not memory_context.strip():
        memory_context = build_agent_memory_context(normalized)
    return run_agent_pipeline(
        agent_id=normalized,
        message=payload.message,
        history=payload.history,
        memory_context=memory_context,
        source=payload.source,
    )


@router.post("/api/chat")
def chat_reply(payload: ChatRequest, _: Annotated[None, Depends(verify_internal_request)]) -> dict[str, Any]:
    normalized = normalize_agent_id(payload.agent_id)
    if not normalized:
        raise HTTPException(status_code=400, detail="Unknown agent id")
    memory_context = getattr(payload, "memory_context", None)
    if not isinstance(memory_context, str) or not memory_context.strip():
        memory_context = build_agent_memory_context(normalized)
    response = run_agent_pipeline(
        agent_id=normalized,
        message=payload.message,
        history=payload.history,
        memory_context=memory_context,
        source="chat",
    )
    return {"agentId": response.agent_id, "model": response.model, "reply": response.reply}


@router.get("/api/integrations/google-calendar/oauth/start")
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


@router.get("/api/integrations/google-calendar/oauth/callback")
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


@router.get("/api/integrations/google-calendar/status")
def google_calendar_status(_: Annotated[None, Depends(verify_internal_request)]) -> dict[str, Any]:
    return {"ok": True, **get_google_calendar_connection_status()}


@router.get("/api/integrations/google-calendar/today")
def google_calendar_today(_: Annotated[None, Depends(verify_internal_request)]) -> dict[str, Any]:
    if not is_google_calendar_enabled():
        raise HTTPException(status_code=400, detail="google_calendar_disabled")
    return {"ok": True, **list_google_today_events()}


@router.post("/api/search", response_model=SearchResponse)
def search_data(
    payload: SearchRequest,
    _: Annotated[None, Depends(verify_internal_request)],
) -> SearchResponse:
    try:
        sanitized_results, provider, filter_stats = get_search_results(query=payload.query, max_results=payload.max_results)
    except SearchProviderError as exc:
        raise HTTPException(status_code=502, detail=f"search provider failure: {exc}") from exc
    return SearchResponse(query=payload.query, results=sanitized_results, provider=provider, filter_stats=filter_stats)


@router.get("/api/runtime-metrics")
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
