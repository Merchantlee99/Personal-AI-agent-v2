from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .pipeline_contract import normalize_event_artifact

ORCHESTRATION_EVENT_SCHEMA_VERSION = 1
REQUIRED_FIELDS = ["agentId", "topicKey", "title", "summary", "priority", "confidence"]
PRIORITY_VALUES = {"critical", "high", "normal", "low"}
PRIORITY_TIER_VALUES = {"P0", "P1", "P2"}
THEME_VALUES = {"morning_briefing", "evening_wrapup", "adhoc"}


def _is_plain_object(value: Any) -> bool:
    return isinstance(value, dict)


def _as_string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _compact(value: str) -> str:
    return " ".join(value.split()).strip()


def _clamp_confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if parsed < 0:
        return 0.0
    if parsed > 1:
        return 1.0
    return parsed


def _looks_like_http_url(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _normalize_source_ref(input_value: Any, index: int, issues: list[str]) -> dict[str, Any] | None:
    if not _is_plain_object(input_value):
        issues.append(f"sourceRefs[{index}] must be object")
        return None

    title = _compact(_as_string(input_value.get("title")))
    url = _compact(_as_string(input_value.get("url")))
    if not title:
        issues.append(f"sourceRefs[{index}].title is required")
    if not url:
        issues.append(f"sourceRefs[{index}].url is required")
    elif not _looks_like_http_url(url):
        issues.append(f"sourceRefs[{index}].url must start with http/https")
    if not title or not url:
        return None

    priority_tier_raw = _compact(_as_string(input_value.get("priorityTier"))).upper()
    priority_tier = priority_tier_raw if priority_tier_raw in PRIORITY_TIER_VALUES else None
    if priority_tier_raw and not priority_tier:
        issues.append(f"sourceRefs[{index}].priorityTier must be one of P0/P1/P2")

    return {
        "title": title,
        "url": url,
        "snippet": _compact(_as_string(input_value.get("snippet"))) or None,
        "publisher": _compact(_as_string(input_value.get("publisher"))) or None,
        "publishedAt": _compact(_as_string(input_value.get("publishedAt"))) or None,
        "category": _compact(_as_string(input_value.get("category"))) or None,
        "priorityTier": priority_tier,
        "domain": _compact(_as_string(input_value.get("domain"))) or None,
    }


def validate_event_contract_v1(raw_body: Any, require_explicit_schema_version: bool = False) -> dict[str, Any]:
    mode = "strict_v1" if require_explicit_schema_version else "legacy_defaulted_v1"
    issues: list[str] = []

    if not _is_plain_object(raw_body):
        return {
            "ok": False,
            "error": "invalid_event_contract",
            "mode": mode,
            "schemaVersion": ORCHESTRATION_EVENT_SCHEMA_VERSION,
            "issues": ["request body must be a JSON object"],
            "required": REQUIRED_FIELDS,
        }

    has_schema_version = "schemaVersion" in raw_body or "schema_version" in raw_body
    schema_version_raw = raw_body.get(
        "schemaVersion",
        raw_body.get("schema_version", ORCHESTRATION_EVENT_SCHEMA_VERSION),
    )
    try:
        schema_version_num = int(schema_version_raw)
    except (TypeError, ValueError):
        schema_version_num = -1

    if require_explicit_schema_version and not has_schema_version:
        issues.append("schemaVersion is required when ORCH_REQUIRE_SCHEMA_V1=true")
    if schema_version_num != ORCHESTRATION_EVENT_SCHEMA_VERSION:
        issues.append(f"unsupported schemaVersion: {schema_version_raw} (supported: 1)")

    agent_id = _compact(_as_string(raw_body.get("agentId")))
    topic_key = _compact(_as_string(raw_body.get("topicKey")))
    title = _compact(_as_string(raw_body.get("title")))
    summary = _compact(_as_string(raw_body.get("summary")))
    priority_raw = _compact(_as_string(raw_body.get("priority"))).lower()
    priority = priority_raw if priority_raw in PRIORITY_VALUES else None

    confidence_raw = raw_body.get("confidence")
    try:
        confidence_numeric = float(confidence_raw)
        confidence_is_finite = True
    except (TypeError, ValueError):
        confidence_numeric = 0.0
        confidence_is_finite = False
    confidence = _clamp_confidence(confidence_numeric)

    if not agent_id:
        issues.append("agentId is required")
    if not topic_key:
        issues.append("topicKey is required")
    if not title:
        issues.append("title is required")
    if not summary:
        issues.append("summary is required")
    if not priority:
        issues.append("priority must be one of critical/high/normal/low")
    if not confidence_is_finite:
        issues.append("confidence must be a finite number")

    tags: list[str] | None = None
    if "tags" in raw_body:
        if not isinstance(raw_body.get("tags"), list):
            issues.append("tags must be an array of strings")
        else:
            tags = [
                _compact(_as_string(item))
                for item in raw_body.get("tags", [])
                if _compact(_as_string(item))
            ][:24]

    source_refs: list[dict[str, Any]] | None = None
    if "sourceRefs" in raw_body:
        if not isinstance(raw_body.get("sourceRefs"), list):
            issues.append("sourceRefs must be an array")
        else:
            source_refs = []
            for index, entry in enumerate(raw_body.get("sourceRefs", [])):
                normalized = _normalize_source_ref(entry, index, issues)
                if normalized:
                    source_refs.append(normalized)
            source_refs = source_refs[:12]

    impact_score: float | None = None
    if "impactScore" in raw_body:
        try:
            value = float(raw_body.get("impactScore"))
        except (TypeError, ValueError):
            issues.append("impactScore must be a finite number")
            value = None
        if value is not None:
            if value < 0 or value > 1:
                issues.append("impactScore must be between 0 and 1")
            else:
                impact_score = value

    payload: dict[str, Any] | None = None
    if "payload" in raw_body:
        if not _is_plain_object(raw_body.get("payload")):
            issues.append("payload must be a JSON object")
        else:
            payload = dict(raw_body.get("payload", {}))

    insight_hint = _compact(_as_string(raw_body.get("insightHint"))) or None
    chat_id = _compact(_as_string(raw_body.get("chatId"))) or None
    force_dispatch = None if "forceDispatch" not in raw_body else bool(raw_body.get("forceDispatch"))
    force_theme_raw = _compact(_as_string(raw_body.get("forceTheme"))).lower()
    force_theme = force_theme_raw if force_theme_raw in THEME_VALUES else None
    if force_theme_raw and not force_theme:
        issues.append("forceTheme must be one of morning_briefing/evening_wrapup/adhoc")

    if issues or not priority:
        return {
            "ok": False,
            "error": "invalid_event_contract",
            "mode": mode,
            "schemaVersion": ORCHESTRATION_EVENT_SCHEMA_VERSION,
            "issues": issues,
            "required": REQUIRED_FIELDS,
        }

    normalized_payload = {
        "schemaVersion": ORCHESTRATION_EVENT_SCHEMA_VERSION,
        "agentId": agent_id,
        "topicKey": topic_key,
        "title": title,
        "summary": summary,
        "priority": priority,
        "confidence": confidence,
        "tags": tags,
        "sourceRefs": source_refs,
        "impactScore": impact_score,
        "insightHint": insight_hint,
        "payload": payload,
        "chatId": chat_id,
        "forceDispatch": force_dispatch,
        "forceTheme": force_theme,
    }
    try:
        normalized_payload = normalize_event_artifact(normalized_payload)
    except ValidationError as exc:
        return {
            "ok": False,
            "error": "invalid_event_artifact",
            "mode": mode,
            "schemaVersion": ORCHESTRATION_EVENT_SCHEMA_VERSION,
            "issues": [str(exc)],
            "required": REQUIRED_FIELDS,
        }

    return {
        "ok": True,
        "mode": "strict_v1" if has_schema_version else "legacy_defaulted_v1",
        "payload": normalized_payload,
    }
