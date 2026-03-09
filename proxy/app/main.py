from __future__ import annotations

import json
import logging
import os
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
from .models import AgentRequest, AgentResponse, ChatRequest, HistoryMessage, SearchRequest, SearchResponse
from .orch_contract import ORCHESTRATION_EVENT_SCHEMA_VERSION, validate_event_contract_v1
from .orch_policy import evaluate_dispatch_policy, get_dispatch_policy, get_journey_theme
from .orch_store import (
    append_agent_event,
    append_morning_briefing_observation,
    append_telegram_chat_history,
    approve_stage_one,
    clear_telegram_chat_history,
    get_clio_claim_review,
    get_clio_note_suggestion,
    create_approval_request,
    create_inbox_task,
    create_event_id,
    dismiss_clio_note_suggestion,
    find_event_by_id,
    get_approval_queue_stats,
    get_approval_request,
    get_cooldown,
    get_telegram_chat_history,
    list_pending_clio_claim_reviews,
    list_pending_clio_note_suggestions,
    list_agent_events,
    make_dedupe_key,
    mark_approval_executed,
    push_digest_item,
    reject_approval_request,
)
from .role_runtime import (
    MODEL_FALLBACKS,
    MODEL_ROUTING,
    ROLE_BOUNDARY,
    build_agent_memory_context as _build_agent_memory_context,
    build_minerva_memory_context as _build_minerva_memory_context,
    record_usage as _record_usage,
    read_bool_env as _read_bool_env,
    read_int_env as _read_int_env,
    run_agent_pipeline as _run_agent_pipeline,
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
from .telegram_runtime import (
    build_calendar_briefing_payload_for_dispatch as _build_calendar_briefing_payload_for_dispatch,
    check_text_rate_limit as _check_text_rate_limit,
    compact_line as _compact_line,
    execute_approval_request as _execute_approval_request,
    execute_inline_action as _execute_inline_action,
    format_telegram_plain_text as _format_telegram_plain_text,
    is_allowed_action as _is_allowed_action,
    render_gcal_status_text as _render_gcal_status_text,
    render_gcal_today_text as _render_gcal_today_text,
    requires_approval as _requires_approval,
    start_clio_alert_loop as _start_clio_alert_loop,
    verify_allowlist as _verify_allowlist,
    verify_approval_source as _verify_approval_source,
    verify_webhook_secret as _verify_webhook_secret,
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


@app.on_event("startup")
def _start_clio_alert_loop_on_startup() -> None:
    _start_clio_alert_loop()


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

    if event.get("theme") == "morning_briefing":
        event_payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        append_morning_briefing_observation(
            {
                "eventId": event["eventId"],
                "topicKey": event["topicKey"],
                "title": event["title"],
                "decision": outcome.get("decision"),
                "reason": outcome.get("reason"),
                "telegram": telegram,
                "calendarBriefingAttached": bool(calendar_briefing is not None),
                "scheduleSlot": event_payload.get("schedule_slot"),
                "priorityTier": event_payload.get("priority_tier"),
                "workflow": event_payload.get("workflow"),
                "bucketCounts": event_payload.get("bucket_counts"),
                "sourceCount": len(event.get("sourceRefs") or []),
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
