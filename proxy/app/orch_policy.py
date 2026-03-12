from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

PRIORITY_WEIGHT = {
    "low": 0,
    "normal": 1,
    "high": 2,
    "critical": 3,
}

DEFAULT_POLICY = {
    "immediateMinPriority": "high",
    "immediateMinConfidence": 0.8,
    "cooldownHours": 8,
    "digestSlots": ["09:00", "18:00"],
}


def _read_optional_number(raw: str | None) -> float | None:
    if raw is None:
        return None
    token = raw.strip()
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _clamp_confidence(value: float) -> float:
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return value


def get_dispatch_policy() -> dict[str, object]:
    confidence = _read_optional_number(os.getenv("MINERVA_IMMEDIATE_MIN_CONFIDENCE"))
    cooldown_hours = _read_optional_number(os.getenv("MINERVA_TOPIC_COOLDOWN_HOURS"))
    slots_raw = (os.getenv("MINERVA_DIGEST_SLOTS") or "").strip()
    min_priority = (os.getenv("MINERVA_IMMEDIATE_MIN_PRIORITY") or "").strip().lower()
    valid_priority = min_priority if min_priority in PRIORITY_WEIGHT else DEFAULT_POLICY["immediateMinPriority"]

    return {
        "immediateMinPriority": valid_priority,
        "immediateMinConfidence": _clamp_confidence(confidence)
        if confidence is not None
        else DEFAULT_POLICY["immediateMinConfidence"],
        "cooldownHours": int(cooldown_hours)
        if cooldown_hours is not None and cooldown_hours > 0
        else DEFAULT_POLICY["cooldownHours"],
        "digestSlots": [token.strip() for token in slots_raw.split(",") if token.strip()]
        if slots_raw
        else list(DEFAULT_POLICY["digestSlots"]),
    }


def get_journey_theme(now: datetime | None = None) -> str:
    timezone = (os.getenv("MINERVA_BRIEFING_TIMEZONE") or "Asia/Seoul").strip() or "Asia/Seoul"
    current = now or datetime.now(ZoneInfo(timezone))
    hour = current.hour
    if 5 <= hour < 12:
        return "morning_briefing"
    if 16 <= hour < 23:
        return "evening_wrapup"
    return "adhoc"


def evaluate_dispatch_policy(
    *,
    priority: str,
    confidence: float,
    policy: dict[str, object],
    cooldown_until: str | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    current = now or datetime.now()

    if cooldown_until:
        try:
            until = datetime.fromisoformat(cooldown_until.replace("Z", "+00:00"))
            if until.timestamp() > current.timestamp():
                return {
                    "decision": "suppressed_cooldown",
                    "reason": "topic_cooldown_active",
                    "mode": "digest",
                    "cooldownUntil": until.isoformat(),
                }
        except ValueError:
            pass

    min_priority = str(policy.get("immediateMinPriority", "high"))
    min_confidence = float(policy.get("immediateMinConfidence", 0.8))
    priority_ok = PRIORITY_WEIGHT.get(priority, 0) >= PRIORITY_WEIGHT.get(min_priority, 2)
    confidence_ok = confidence >= min_confidence
    if priority_ok and confidence_ok:
        return {
            "decision": "send_now",
            "reason": "priority_and_confidence_threshold",
            "mode": "immediate",
        }

    return {
        "decision": "queue_digest",
        "reason": "below_immediate_threshold",
        "mode": "digest",
    }
