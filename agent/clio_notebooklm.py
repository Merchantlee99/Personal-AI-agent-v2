from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    token = raw.strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_language(value: str) -> str:
    token = re.sub(r"[^A-Za-z]", "", value).lower()
    if not token:
        return "unknown"
    return token


def detect_source_language(message: str) -> str:
    explicit_patterns = (
        r"source[_\s-]?lang(?:uage)?\s*[:=]\s*([A-Za-z-]{2,10})",
        r"\[(?:source[_\s-]?lang(?:uage)?)\s*[:=]\s*([A-Za-z-]{2,10})\]",
    )
    for pattern in explicit_patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return _normalize_language(match.group(1))

    if re.search(r"[가-힣]", message):
        return "ko"
    if re.search(r"[ぁ-んァ-ン一-龥]", message):
        return "ja"
    return "en"


def translate_with_deepl(text: str, source_language: str, target_language: str) -> str | None:
    api_key = os.getenv("DEEPL_API_KEY", "").strip()
    if not api_key:
        return None

    payload: list[tuple[str, str]] = [("text", text), ("target_lang", target_language.upper())]
    source_token = _normalize_language(source_language).upper()
    if source_token not in {"", "UNKNOWN", "AUTO"}:
        payload.append(("source_lang", source_token))

    glossary_id = os.getenv("DEEPL_GLOSSARY_ID", "").strip()
    if glossary_id:
        payload.append(("glossary_id", glossary_id))

    request = urllib.request.Request(
        "https://api-free.deepl.com/v2/translate",
        data=urllib.parse.urlencode(payload, doseq=True).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"DeepL-Auth-Key {api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=8) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None

    translations = body.get("translations")
    if not isinstance(translations, list) or not translations:
        return None
    first = translations[0]
    if not isinstance(first, dict):
        return None
    translated = str(first.get("text", "")).strip()
    return translated or None


def dispatch_notebooklm_sync(payload: dict[str, object]) -> dict[str, object]:
    enabled = parse_bool_env("NOTEBOOKLM_SYNC_ENABLED", False)
    if not enabled:
        return {"attempted": False, "delivered": False, "reason": "disabled"}

    endpoint = os.getenv("NOTEBOOKLM_INGEST_WEBHOOK_URL", "").strip()
    if not endpoint:
        return {"attempted": False, "delivered": False, "reason": "missing_endpoint"}

    timeout_raw = os.getenv("NOTEBOOKLM_TIMEOUT_SEC", "8").strip()
    try:
        timeout_sec = max(1.0, float(timeout_raw))
    except ValueError:
        timeout_sec = 8.0

    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("NOTEBOOKLM_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = {
        "source": "nanoclaw-clio",
        "generated_at": payload.get("generated_at"),
        "agent_id": payload.get("agent_id"),
        "title": (payload.get("notebooklm") or {}).get("title"),
        "summary": (payload.get("notebooklm") or {}).get("summary"),
        "vault_file": (payload.get("notebooklm") or {}).get("vault_file"),
        "tags": payload.get("tags", []),
        "source_urls": payload.get("source_urls", []),
    }

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:  # noqa: S310
            status = int(getattr(response, "status", 0) or 0)
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"attempted": True, "delivered": False, "reason": "request_failed"}

    if 200 <= status < 300:
        return {"attempted": True, "delivered": True, "reason": "ok", "status": status}
    return {"attempted": True, "delivered": False, "reason": "non_2xx", "status": status}
