from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from .llm_client import FatalLLMError, RetryableLLMError, generate_agent_reply
from .models import AgentResponse, HistoryMessage
from .orch_store import (
    get_clio_knowledge_memory,
    get_hermes_evidence_memory,
    get_minerva_working_memory,
    render_clio_knowledge_memory_context,
    render_hermes_evidence_memory_context,
    render_minerva_working_memory_context,
)

logger = logging.getLogger("nanoclaw.llm_proxy")


def read_int_env(name: str, default: int, minimum: int = 0) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(float(raw))
    except ValueError:
        return default
    return max(minimum, parsed)


def read_bool_env(name: str, fallback: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return fallback
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return fallback


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


METRICS_STORE_PATH = os.getenv("LLM_USAGE_STORE_PATH", "").strip()


def record_usage(
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


def run_agent_pipeline(
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
            record_usage(
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
            record_usage(
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
            record_usage(
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
        record_usage(
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


def build_minerva_memory_context() -> str | None:
    return render_minerva_working_memory_context(get_minerva_working_memory())


def build_clio_memory_context() -> str | None:
    return render_clio_knowledge_memory_context(get_clio_knowledge_memory())


def build_hermes_memory_context(topic_key: str | None = None) -> str | None:
    return render_hermes_evidence_memory_context(get_hermes_evidence_memory(), topic_key=topic_key)


def build_agent_memory_context(agent_id: str, *, topic_key: str | None = None) -> str | None:
    if agent_id == "minerva":
        return build_minerva_memory_context()
    if agent_id == "clio":
        return build_clio_memory_context()
    if agent_id == "hermes":
        return build_hermes_memory_context(topic_key=topic_key)
    return None
