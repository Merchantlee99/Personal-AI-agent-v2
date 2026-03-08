from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .models import SearchResult

PROMPT_LIKE_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?(previous|above|system|developer)\s+instructions?", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"developer\s+message", re.IGNORECASE),
    re.compile(r"```[\s\S]*?```", re.IGNORECASE),
    re.compile(r"<script[\s\S]*?>[\s\S]*?</script>", re.IGNORECASE),
    re.compile(r"javascript:", re.IGNORECASE),
    re.compile(r"\b(bash|sudo|rm\s+-rf|curl\s+https?://|wget\s+https?://)\b", re.IGNORECASE),
)


class SearchProviderError(Exception):
    pass


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _compact_text(value: str, *, limit: int) -> str:
    collapsed = re.sub(r"\s+", " ", value).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 1)].rstrip() + "…"


def _allowed_tavily_hosts() -> set[str]:
    raw = (os.getenv("TAVILY_API_ALLOWED_HOSTS") or "api.tavily.com").strip()
    hosts = {
        item.strip().lower()
        for item in raw.split(",")
        if item.strip()
    }
    return hosts or {"api.tavily.com"}


def _strip_prompt_like(value: str, stats: dict[str, int]) -> str:
    text = value
    for pattern in PROMPT_LIKE_PATTERNS:
        text, changed = pattern.subn(" ", text)
        if changed:
            stats["prompt_like_removed"] = stats.get("prompt_like_removed", 0) + changed
    return text


def _is_public_http_url(raw_url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(raw_url.strip())
    except ValueError:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False
    if hostname == "localhost" or hostname.endswith(".local"):
        return False

    try:
        ip = ipaddress.ip_address(hostname)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    except ValueError:
        pass

    return True


def _validate_tavily_api_base(raw_url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(raw_url.strip())
    except ValueError as exc:
        raise SearchProviderError("invalid tavily api base") from exc

    if parsed.scheme != "https":
        raise SearchProviderError("tavily api base must use https")

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise SearchProviderError("tavily api base missing host")

    if hostname == "localhost" or hostname.endswith(".local"):
        raise SearchProviderError("tavily api base host is not allowed")

    try:
        ip = ipaddress.ip_address(hostname)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise SearchProviderError("tavily api base host is not allowed")
    except ValueError:
        pass

    allowed_hosts = _allowed_tavily_hosts()
    if hostname not in allowed_hosts:
        raise SearchProviderError(f"tavily api base host not allowlisted: {hostname}")

    return raw_url.strip().rstrip("/")


def _sanitize_results(raw_results: list[dict[str, object]], max_results: int) -> tuple[list[SearchResult], dict[str, int]]:
    stats: dict[str, int] = {
        "input_count": len(raw_results),
        "kept_count": 0,
        "dropped_count": 0,
        "dropped_unsafe_url": 0,
        "dropped_empty": 0,
        "prompt_like_removed": 0,
    }
    sanitized: list[SearchResult] = []

    for entry in raw_results:
        title_raw = str(entry.get("title", "")).strip()
        url_raw = str(entry.get("url", "")).strip()
        snippet_raw = str(entry.get("snippet", entry.get("content", ""))).strip()

        if not title_raw or not url_raw:
            stats["dropped_count"] += 1
            stats["dropped_empty"] += 1
            continue
        if not _is_public_http_url(url_raw):
            stats["dropped_count"] += 1
            stats["dropped_unsafe_url"] += 1
            continue

        title = _compact_text(_strip_prompt_like(title_raw, stats), limit=180)
        snippet = _compact_text(_strip_prompt_like(snippet_raw, stats), limit=400)
        if not snippet:
            snippet = "원문 요약이 비어 있어 기본 요약으로 대체되었습니다."
        if not title:
            title = "untitled-result"

        sanitized.append(SearchResult(title=title, url=url_raw, snippet=snippet))
        if len(sanitized) >= max_results:
            break

    stats["kept_count"] = len(sanitized)
    return sanitized, stats


def _mock_results(query: str, max_results: int) -> tuple[list[SearchResult], dict[str, int]]:
    rows = [
        SearchResult(
            title=f"Search sample {index + 1}",
            url=f"https://example.com/search/{index + 1}",
            snippet=(
                f"Query={query}. Potential prompt-like text is preserved as plain data and never executed."
            ),
        )
        for index in range(max_results)
    ]
    stats = {
        "input_count": max_results,
        "kept_count": max_results,
        "dropped_count": 0,
        "dropped_unsafe_url": 0,
        "dropped_empty": 0,
        "prompt_like_removed": 0,
    }
    return rows, stats


def _fetch_tavily_raw(*, query: str, max_results: int) -> list[dict[str, object]]:
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise SearchProviderError("TAVILY_API_KEY is missing")

    api_base = _validate_tavily_api_base(os.getenv("TAVILY_API_BASE", "https://api.tavily.com"))
    endpoint = f"{api_base}/search"
    timeout_sec = max(1.0, _env_float("SEARCH_TIMEOUT_SEC", 8.0))
    search_depth = os.getenv("TAVILY_SEARCH_DEPTH", "basic").strip() or "basic"

    request_body = json.dumps(
        {
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": search_depth,
            "include_answer": False,
            "include_raw_content": False,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=request_body,
        headers={"content-type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="ignore")
        raise SearchProviderError(f"tavily http error: {err.code} {detail[:200]}") from err
    except (urllib.error.URLError, TimeoutError) as err:
        raise SearchProviderError(f"tavily transport error: {err}") from err

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as err:
        raise SearchProviderError("invalid json from tavily") from err

    results = payload.get("results")
    if not isinstance(results, list):
        raise SearchProviderError("tavily response missing results")

    normalized: list[dict[str, object]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", item.get("snippet", "")),
            }
        )
    return normalized


def get_search_results(*, query: str, max_results: int) -> tuple[list[SearchResult], str, dict[str, int]]:
    provider = os.getenv("SEARCH_PROVIDER", "auto").strip().lower() or "auto"
    tavily_enabled = bool(os.getenv("TAVILY_API_KEY", "").strip())

    if provider == "mock":
        rows, stats = _mock_results(query, max_results)
        return rows, "mock", stats

    if provider == "tavily" and not tavily_enabled:
        raise SearchProviderError("SEARCH_PROVIDER=tavily but TAVILY_API_KEY is missing")

    if provider in {"auto", "tavily"} and tavily_enabled:
        try:
            raw = _fetch_tavily_raw(query=query, max_results=max_results)
            rows, stats = _sanitize_results(raw, max_results=max_results)
            return rows, "tavily", stats
        except SearchProviderError:
            if provider == "tavily":
                raise

    rows, stats = _mock_results(query, max_results)
    return rows, "mock", stats
