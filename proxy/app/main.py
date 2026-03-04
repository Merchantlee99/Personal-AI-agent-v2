import json
import os
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException

from .agents import AGENT_REGISTRY, normalize_agent_id
from .llm_client import FatalLLMError, RetryableLLMError, generate_agent_reply
from .models import AgentRequest, AgentResponse, SearchRequest, SearchResponse
from .search_client import SearchProviderError, get_search_results
from .security import verify_internal_request

app = FastAPI(title="nanoclaw-llm-proxy", version="2.0.0")
logger = logging.getLogger("nanoclaw.llm_proxy")
METRICS_STORE_PATH = os.getenv("LLM_USAGE_STORE_PATH", "").strip()


def _parse_model_fallbacks(name: str) -> list[str]:
    raw = os.getenv(name, "").strip()
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "llm-proxy", "version": "2.0.0"}


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
    return {
        "canonical_ids": list(AGENT_REGISTRY.keys()),
        "aliases": {},
        "agents": agents,
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
    now = datetime.now(UTC)
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


@app.post("/api/agent", response_model=AgentResponse)
def agent_reply(
    payload: AgentRequest,
    _: Annotated[None, Depends(verify_internal_request)],
) -> AgentResponse:
    normalized = normalize_agent_id(payload.agent_id)
    if not normalized:
        raise HTTPException(status_code=400, detail="Unknown agent id")

    model_candidates = _model_candidates(normalized)
    configured_model = model_candidates[0]
    selected_model = configured_model
    reply: str | None = None
    last_retryable: RetryableLLMError | None = None
    quota_429_hits = 0

    for index, model in enumerate(model_candidates):
        selected_model = model
        try:
            reply = generate_agent_reply(
                agent_id=normalized,
                model=model,
                role_boundary=ROLE_BOUNDARY[normalized],
                message=payload.message,
                history=payload.history,
                memory_context=payload.memory_context,
            )
            if index > 0:
                logger.warning(
                    "model_fallback_applied agent=%s selected_model=%s primary_model=%s",
                    normalized,
                    model,
                    model_candidates[0],
                )
            _record_usage(
                agent_id=normalized,
                configured_model=configured_model,
                selected_model=selected_model,
                status="success",
                quota_429_hits=quota_429_hits,
            )
            break
        except RetryableLLMError as exc:
            last_retryable = exc
            logger.warning("retryable_llm_error agent=%s model=%s detail=%s", normalized, model, exc)
            if _is_quota_error(exc):
                quota_429_hits += 1
            should_try_fallback = index < len(model_candidates) - 1 and _is_quota_error(exc)
            if should_try_fallback:
                logger.warning(
                    "model_fallback_triggered agent=%s from_model=%s to_model=%s reason=%s",
                    normalized,
                    model,
                    model_candidates[index + 1],
                    exc,
                )
                continue
            _record_usage(
                agent_id=normalized,
                configured_model=configured_model,
                selected_model=selected_model,
                status="transient_error",
                quota_429_hits=quota_429_hits,
                error_detail=str(exc),
            )
            raise HTTPException(status_code=502, detail=f"LLM transient failure: {exc}") from exc
        except FatalLLMError as exc:
            logger.error("fatal_llm_error agent=%s model=%s detail=%s", normalized, model, exc)
            _record_usage(
                agent_id=normalized,
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
            agent_id=normalized,
            configured_model=configured_model,
            selected_model=selected_model,
            status="transient_error",
            quota_429_hits=quota_429_hits,
            error_detail=detail,
        )
        raise HTTPException(status_code=502, detail=detail)

    return AgentResponse(
        agent_id=normalized,
        model=selected_model,
        reply=reply,
        role_boundary=ROLE_BOUNDARY[normalized],
    )


@app.post("/api/search", response_model=SearchResponse)
def search_data(
    payload: SearchRequest,
    _: Annotated[None, Depends(verify_internal_request)],
) -> SearchResponse:
    try:
        sanitized_results, provider, filter_stats = get_search_results(
            query=payload.query,
            max_results=payload.max_results,
        )
    except SearchProviderError as exc:
        raise HTTPException(status_code=502, detail=f"search provider failure: {exc}") from exc

    return SearchResponse(
        query=payload.query,
        results=sanitized_results,
        provider=provider,
        filter_stats=filter_stats,
    )
