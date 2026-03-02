import os
import logging
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException

from .agents import AGENT_REGISTRY, LEGACY_ALIASES, normalize_agent_id
from .llm_client import FatalLLMError, RetryableLLMError, generate_agent_reply
from .models import AgentRequest, AgentResponse, SearchRequest, SearchResponse, SearchResult
from .security import verify_internal_request

app = FastAPI(title="nanoclaw-llm-proxy", version="2.0.0")
logger = logging.getLogger("nanoclaw.llm_proxy")

MODEL_ROUTING = {
    "minerva": os.getenv("MODEL_MINERVA", "gemini-2.0-flash"),
    "clio": os.getenv("MODEL_CLIO", "gemini-2.0-flash-lite"),
    "hermes": os.getenv("MODEL_HERMES", "gemini-2.0-flash"),
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
        "aliases": LEGACY_ALIASES,
        "agents": agents,
    }


@app.post("/api/agent", response_model=AgentResponse)
def agent_reply(
    payload: AgentRequest,
    _: Annotated[None, Depends(verify_internal_request)],
) -> AgentResponse:
    normalized = normalize_agent_id(payload.agent_id)
    if not normalized:
        raise HTTPException(status_code=400, detail="Unknown agent id")

    model = MODEL_ROUTING[normalized]
    try:
        reply = generate_agent_reply(
            agent_id=normalized,
            model=model,
            role_boundary=ROLE_BOUNDARY[normalized],
            message=payload.message,
            history=payload.history,
        )
    except RetryableLLMError as exc:
        logger.warning("retryable_llm_error agent=%s model=%s detail=%s", normalized, model, exc)
        raise HTTPException(status_code=502, detail=f"LLM transient failure: {exc}") from exc
    except FatalLLMError as exc:
        logger.error("fatal_llm_error agent=%s model=%s detail=%s", normalized, model, exc)
        raise HTTPException(status_code=502, detail=f"LLM fatal failure: {exc}") from exc

    return AgentResponse(
        agent_id=normalized,
        model=model,
        reply=reply,
        role_boundary=ROLE_BOUNDARY[normalized],
    )


@app.post("/api/search", response_model=SearchResponse)
def search_data(
    payload: SearchRequest,
    _: Annotated[None, Depends(verify_internal_request)],
) -> SearchResponse:
    # External search responses are transformed into inert data records.
    sanitized_results: list[SearchResult] = []
    for index in range(payload.max_results):
        sanitized_results.append(
            SearchResult(
                title=f"Search sample {index + 1}",
                url=f"https://example.com/search/{index + 1}",
                snippet=(
                    f"Query={payload.query}. Potential prompt-like text is preserved as plain data, "
                    "never executed as an instruction."
                ),
            )
        )

    return SearchResponse(query=payload.query, results=sanitized_results)
