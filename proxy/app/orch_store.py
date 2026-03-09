from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .orch_memory import (
    CLIO_KNOWLEDGE_MEMORY_FILE,
    HERMES_EVIDENCE_MEMORY_FILE,
    LOGS_DIR,
    MEMORY_DIR,
    MINERVA_WORKING_MEMORY_FILE,
    MORNING_BRIEFING_OBSERVATIONS_FILE,
    ROOT,
    append_agent_event,
    append_jsonl_file as _append_jsonl_file,
    append_telegram_chat_history,
    clear_telegram_chat_history,
    create_event_id,
    create_inbox_task,
    default_clio_knowledge_memory,
    default_minerva_working_memory,
    find_event_by_id,
    get_cooldown,
    get_hermes_evidence_memory,
    get_runtime_memory_markdown_path,
    get_telegram_chat_history,
    has_meaningful_value as _has_meaningful_value,
    list_agent_events,
    make_dedupe_key,
    normalize_clio_knowledge_memory,
    normalize_hermes_evidence_memory,
    normalize_minerva_working_memory,
    normalize_string_list as _normalize_string_list,
    parse_iso_datetime as _parse_iso_datetime,
    push_digest_item,
    read_json_file as _read_json_file,
    render_hermes_evidence_memory_context,
    render_minerva_working_memory_context,
    safe_float as _safe_float,
    sanitize_text as _sanitize_text,
    set_cooldown,
    single_line as _single_line,
    upsert_hermes_evidence_memory,
    write_json_file as _write_json_file,
)
from .pipeline_contract import normalize_approval_request_artifact

APPROVAL_QUEUE_FILE = MEMORY_DIR / "approval_queue.json"
CLIO_CLAIM_REVIEW_QUEUE_FILE = MEMORY_DIR / "clio_claim_review_queue.json"
CLIO_ALERT_STATE_FILE = MEMORY_DIR / "clio_alert_state.json"
APPROVAL_TTL_SEC = max(60, int(float(os.getenv("TELEGRAM_APPROVAL_TTL_SEC", "300") or "300")))
APPROVAL_RETENTION_HOURS = max(
    1, int(float(os.getenv("TELEGRAM_APPROVAL_RETENTION_HOURS", "72") or "72"))
)
CLIO_SUGGESTION_DISMISS_COOLDOWN_SEC = max(
    300, int(float(os.getenv("CLIO_SUGGESTION_DISMISS_COOLDOWN_SEC", "43200") or "43200"))
)


def append_morning_briefing_observation(observation: dict[str, Any]) -> None:
    payload = {
        "observedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        **observation,
    }
    _append_jsonl_file(MORNING_BRIEFING_OBSERVATIONS_FILE, payload)


def get_minerva_working_memory() -> dict[str, Any]:
    payload = _read_json_file(MINERVA_WORKING_MEMORY_FILE, default_minerva_working_memory())
    return normalize_minerva_working_memory(payload if isinstance(payload, dict) else None)


def set_minerva_working_memory(payload: dict[str, Any] | None) -> dict[str, Any]:
    memory = normalize_minerva_working_memory(payload)
    MINERVA_WORKING_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MINERVA_WORKING_MEMORY_FILE.with_suffix(MINERVA_WORKING_MEMORY_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MINERVA_WORKING_MEMORY_FILE)
    try:
        os.chmod(MINERVA_WORKING_MEMORY_FILE, 0o600)
    except OSError:
        pass
    return memory


def get_clio_knowledge_memory() -> dict[str, Any]:
    payload = _read_json_file(CLIO_KNOWLEDGE_MEMORY_FILE, default_clio_knowledge_memory())
    return normalize_clio_knowledge_memory(payload if isinstance(payload, dict) else None)


def _approval_is_pending(status: str) -> bool:
    return status in {"pending_stage1", "pending_stage2"}


def _default_approval_store() -> dict[str, Any]:
    return {"updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "approvals": {}}


def _prune_approval_store(store: dict[str, Any], now: datetime) -> dict[str, Any]:
    approvals = store.get("approvals")
    if not isinstance(approvals, dict):
        store["approvals"] = {}
        approvals = store["approvals"]
    dirty = False
    retention_ms = APPROVAL_RETENTION_HOURS * 3600 * 1000
    now_ms = int(now.timestamp() * 1000)

    for approval_id, approval in list(approvals.items()):
        if not isinstance(approval, dict):
            del approvals[approval_id]
            dirty = True
            continue
        expires_at = str(approval.get("expiresAt", ""))
        status = str(approval.get("status", ""))
        try:
            expires_ms = int(datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            expires_ms = 0

        if _approval_is_pending(status) and expires_ms and expires_ms <= now_ms:
            approval["status"] = "expired"
            history = approval.get("history")
            if not isinstance(history, list):
                history = []
            history.append({"at": now.isoformat().replace("+00:00", "Z"), "type": "expired"})
            approval["history"] = history
            approvals[approval_id] = approval
            dirty = True
            continue

        requested_at = str(approval.get("requestedAt", ""))
        try:
            requested_ms = int(datetime.fromisoformat(requested_at.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            requested_ms = 0
        if not _approval_is_pending(status) and requested_ms and now_ms - requested_ms > retention_ms:
            del approvals[approval_id]
            dirty = True

    if dirty:
        store["updatedAt"] = now.isoformat().replace("+00:00", "Z")
    return store


def _read_approval_store() -> dict[str, Any]:
    raw = _read_json_file(APPROVAL_QUEUE_FILE, _default_approval_store())
    if not isinstance(raw, dict):
        raw = _default_approval_store()
    if not isinstance(raw.get("approvals"), dict):
        raw["approvals"] = {}
    return _prune_approval_store(raw, datetime.now(timezone.utc))


def _write_approval_store(store: dict[str, Any]) -> None:
    store["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_json_file(APPROVAL_QUEUE_FILE, store)


def _approval_required_steps() -> int:
    try:
        raw = int(float(os.getenv("TELEGRAM_APPROVAL_REQUIRED_STEPS", "2") or "2"))
    except ValueError:
        raw = 2
    return 2 if raw >= 2 else 1


def create_approval_request(
    *,
    action: str,
    event_id: str,
    event_title: str,
    topic_key: str,
    chat_id: str,
    requested_by_user_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store = _read_approval_store()
    approvals = store.get("approvals", {})

    for existing in approvals.values():
        if not isinstance(existing, dict):
            continue
        if (
            existing.get("action") == action
            and existing.get("eventId") == event_id
            and existing.get("chatId") == chat_id
            and existing.get("requestedByUserId") == requested_by_user_id
            and _approval_is_pending(str(existing.get("status")))
        ):
            return {"approval": existing, "reused": True}

    now = datetime.now(timezone.utc)
    approval = {
        "id": secrets.token_hex(6),
        "action": action,
        "eventId": event_id,
        "eventTitle": event_title,
        "topicKey": topic_key,
        "chatId": chat_id,
        "requestedByUserId": requested_by_user_id,
        "requestedAt": now.isoformat().replace("+00:00", "Z"),
        "expiresAt": (now + timedelta(seconds=APPROVAL_TTL_SEC)).isoformat().replace("+00:00", "Z"),
        "requiredSteps": _approval_required_steps(),
        "status": "pending_stage1",
        "payload": payload if isinstance(payload, dict) and payload else None,
        "history": [{"at": now.isoformat().replace("+00:00", "Z"), "type": "created", "actorUserId": requested_by_user_id}],
    }
    approval = normalize_approval_request_artifact(approval)
    approvals[approval["id"]] = approval
    store["approvals"] = approvals
    _write_approval_store(store)
    return {"approval": approval, "reused": False}


def get_approval_request(approval_id: str) -> dict[str, Any] | None:
    store = _read_approval_store()
    approvals = store.get("approvals", {})
    value = approvals.get(approval_id) if isinstance(approvals, dict) else None
    return value if isinstance(value, dict) else None


def _update_approval_status(approval_id: str, status: str, history_type: str, actor_user_id: str) -> dict[str, Any] | None:
    store = _read_approval_store()
    approvals = store.get("approvals", {})
    if not isinstance(approvals, dict):
        return None
    found = approvals.get(approval_id)
    if not isinstance(found, dict):
        return None
    if found.get("status") == status:
        return found
    found["status"] = status
    history = found.get("history")
    if not isinstance(history, list):
        history = []
    history.append({"at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "type": history_type, "actorUserId": actor_user_id})
    found["history"] = history
    approvals[approval_id] = found
    store["approvals"] = approvals
    _write_approval_store(store)
    return found


def approve_stage_one(approval_id: str, actor_user_id: str) -> dict[str, Any] | None:
    current = get_approval_request(approval_id)
    if not current:
        return None
    if current.get("status") != "pending_stage1":
        return current
    return _update_approval_status(approval_id, "pending_stage2", "stage1_approved", actor_user_id)


def reject_approval_request(approval_id: str, actor_user_id: str) -> dict[str, Any] | None:
    current = get_approval_request(approval_id)
    if not current:
        return None
    if current.get("status") == "rejected":
        return current
    return _update_approval_status(approval_id, "rejected", "rejected", actor_user_id)


def mark_approval_executed(approval_id: str, actor_user_id: str) -> dict[str, Any] | None:
    current = get_approval_request(approval_id)
    if not current:
        return None
    if current.get("status") == "executed":
        return current
    return _update_approval_status(approval_id, "executed", "executed", actor_user_id)


def list_pending_approvals(limit: int = 60) -> list[dict[str, Any]]:
    store = _read_approval_store()
    approvals = store.get("approvals", {})
    if not isinstance(approvals, dict):
        return []
    pending = [
        value
        for value in approvals.values()
        if isinstance(value, dict) and _approval_is_pending(str(value.get("status")))
    ]
    pending.sort(key=lambda item: str(item.get("requestedAt", "")), reverse=True)
    return pending[: max(1, limit)]


def get_approval_queue_stats() -> dict[str, Any]:
    store = _read_approval_store()
    approvals = [item for item in store.get("approvals", {}).values() if isinstance(item, dict)]
    stats = {
        "pending": 0,
        "pendingStage1": 0,
        "pendingStage2": 0,
        "executed": 0,
        "rejected": 0,
        "expired": 0,
        "total": len(approvals),
        "updatedAt": store.get("updatedAt"),
    }
    for approval in approvals:
        status = str(approval.get("status"))
        if status == "pending_stage1":
            stats["pending"] += 1
            stats["pendingStage1"] += 1
        elif status == "pending_stage2":
            stats["pending"] += 1
            stats["pendingStage2"] += 1
        elif status == "executed":
            stats["executed"] += 1
        elif status == "rejected":
            stats["rejected"] += 1
        elif status == "expired":
            stats["expired"] += 1
    return stats


def _default_clio_claim_review_queue() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": [],
    }


def normalize_clio_claim_review_queue(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    queue = _default_clio_claim_review_queue()
    queue["schemaVersion"] = int(raw.get("schemaVersion", 1) or 1)
    queue["updatedAt"] = _sanitize_text(raw.get("updatedAt"), 64) or queue["updatedAt"]

    items: list[dict[str, Any]] = []
    for item in raw.get("items") or []:
        if not isinstance(item, dict):
            continue
        review_id = _sanitize_text(item.get("id"), 32)
        title = _sanitize_text(item.get("title"), 180)
        topic_key = _sanitize_text(item.get("topicKey"), 120)
        vault_file = _sanitize_text(item.get("vaultFile"), 260)
        if not review_id or not title or not topic_key or not vault_file:
            continue
        items.append(
            {
                "id": review_id,
                "status": _sanitize_text(item.get("status"), 40) or "pending_user_review",
                "title": title,
                "topicKey": topic_key,
                "vaultFile": vault_file,
                "sourceUrls": _normalize_string_list(item.get("sourceUrls"), limit=8, item_limit=240),
                "projectLinks": _normalize_string_list(item.get("projectLinks"), limit=6, item_limit=120),
                "mocCandidates": _normalize_string_list(item.get("mocCandidates"), limit=6, item_limit=120),
                "requestedAt": _sanitize_text(item.get("requestedAt"), 64),
                "confirmedAt": _sanitize_text(item.get("confirmedAt"), 64),
                "confirmedByUserId": _sanitize_text(item.get("confirmedByUserId"), 80),
                "history": item.get("history") if isinstance(item.get("history"), list) else [],
            }
        )
        if len(items) >= 200:
            break
    queue["items"] = items
    return queue


def _read_clio_claim_review_queue() -> dict[str, Any]:
    payload = _read_json_file(CLIO_CLAIM_REVIEW_QUEUE_FILE, _default_clio_claim_review_queue())
    return normalize_clio_claim_review_queue(payload if isinstance(payload, dict) else None)


def _write_clio_claim_review_queue(queue: dict[str, Any]) -> None:
    queue["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_json_file(CLIO_CLAIM_REVIEW_QUEUE_FILE, queue)


def get_clio_claim_review(review_id: str) -> dict[str, Any] | None:
    review_id = _sanitize_text(review_id, 32)
    if not review_id:
        return None
    for item in _read_clio_claim_review_queue().get("items", []):
        if isinstance(item, dict) and item.get("id") == review_id:
            return item
    return None


def list_pending_clio_claim_reviews(limit: int = 8) -> list[dict[str, Any]]:
    items = [
        item
        for item in _read_clio_claim_review_queue().get("items", [])
        if isinstance(item, dict) and str(item.get("status")) == "pending_user_review"
    ]
    items.sort(key=lambda row: str(row.get("requestedAt", "")), reverse=True)
    return items[: max(1, limit)]


def _default_clio_alert_state() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "claimReviewAlerts": {},
        "noteSuggestionAlerts": {},
    }


def _read_clio_alert_state() -> dict[str, Any]:
    payload = _read_json_file(CLIO_ALERT_STATE_FILE, _default_clio_alert_state())
    if not isinstance(payload, dict):
        return _default_clio_alert_state()
    normalized = _default_clio_alert_state()
    normalized["updatedAt"] = _sanitize_text(payload.get("updatedAt"), 64) or normalized["updatedAt"]
    for key in ("claimReviewAlerts", "noteSuggestionAlerts"):
        source = payload.get(key)
        if not isinstance(source, dict):
            continue
        normalized[key] = {
            _sanitize_text(item_key, 64): {
                "fingerprint": _sanitize_text(item_value.get("fingerprint"), 64),
                "sentAt": _sanitize_text(item_value.get("sentAt"), 64),
            }
            for item_key, item_value in source.items()
            if _sanitize_text(item_key, 64) and isinstance(item_value, dict)
        }
    return normalized


def _write_clio_alert_state(state: dict[str, Any]) -> None:
    state["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_json_file(CLIO_ALERT_STATE_FILE, state)


def list_new_clio_claim_review_alerts(limit: int = 4) -> list[dict[str, Any]]:
    state = _read_clio_alert_state()
    seen = state.get("claimReviewAlerts") if isinstance(state.get("claimReviewAlerts"), dict) else {}
    new_items: list[dict[str, Any]] = []
    for review in list_pending_clio_claim_reviews(limit=50):
        review_id = _sanitize_text(review.get("id"), 32)
        if not review_id or review_id in seen:
            continue
        new_items.append(review)
        if len(new_items) >= max(1, limit):
            break
    return new_items


def list_new_clio_note_suggestion_alerts(limit: int = 4) -> list[dict[str, Any]]:
    state = _read_clio_alert_state()
    seen = state.get("noteSuggestionAlerts") if isinstance(state.get("noteSuggestionAlerts"), dict) else {}
    new_items: list[dict[str, Any]] = []
    for suggestion in list_pending_clio_note_suggestions(limit=50):
        suggestion_id = _sanitize_text(suggestion.get("id"), 32)
        if not suggestion_id:
            continue
        fingerprint = _sanitize_text(suggestion.get("suggestionFingerprint"), 64)
        existing = seen.get(suggestion_id) if isinstance(seen.get(suggestion_id), dict) else {}
        if _sanitize_text(existing.get("fingerprint"), 64) == fingerprint:
            continue
        new_items.append(suggestion)
        if len(new_items) >= max(1, limit):
            break
    return new_items


def mark_clio_alert_sent(kind: str, item_id: str, *, fingerprint: str | None = None) -> None:
    state = _read_clio_alert_state()
    bucket_key = "claimReviewAlerts" if kind == "claim_review" else "noteSuggestionAlerts"
    bucket = state.get(bucket_key) if isinstance(state.get(bucket_key), dict) else {}
    bucket[_sanitize_text(item_id, 64)] = {
        "fingerprint": _sanitize_text(fingerprint, 64),
        "sentAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    state[bucket_key] = bucket
    _write_clio_alert_state(state)


def _vault_root() -> Path:
    return (ROOT / "obsidian_vault").resolve()


def _safe_vault_path(relative_or_absolute: str) -> Path | None:
    raw = _sanitize_text(relative_or_absolute, 260)
    if not raw:
        return None
    candidate = Path(raw)
    resolved = candidate.resolve() if candidate.is_absolute() else (ROOT / candidate).resolve()
    try:
        resolved.relative_to(_vault_root())
    except ValueError:
        return None
    return resolved


def _update_frontmatter_scalar(markdown: str, key: str, value: str) -> str:
    match = re.match(r"(?s)\A---\n(.*?)\n---\n?", markdown)
    if not match:
        return markdown
    lines = match.group(1).splitlines()
    replacement = f'{key}: {json.dumps(str(value), ensure_ascii=False)}'
    next_lines: list[str] = []
    found = False
    for line in lines:
        if re.match(rf"^{re.escape(key)}:\s*", line):
            next_lines.append(replacement)
            found = True
            continue
        next_lines.append(line)
    if not found:
        next_lines.append(replacement)
    frontmatter = "---\n" + "\n".join(next_lines) + "\n---\n"
    return frontmatter + markdown[match.end() :]


def _apply_clio_note_draft_state(vault_file: str, draft_state: str) -> str | None:
    note_path = _safe_vault_path(vault_file)
    if not note_path or not note_path.is_file():
        return None
    markdown = note_path.read_text(encoding="utf-8")
    updated = _update_frontmatter_scalar(markdown, "draft_state", draft_state)
    updated = _update_frontmatter_scalar(updated, "updated", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    note_path.write_text(updated, encoding="utf-8")
    return str(note_path)


def _update_clio_knowledge_memory_claim(review_id: str, *, draft_state: str, claim_review_required: bool) -> None:
    memory = get_clio_knowledge_memory()
    changed = False
    for item in memory.get("recentNotes", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("claimReviewId") or "") != review_id:
            continue
        item["draftState"] = draft_state
        item["claimReviewRequired"] = claim_review_required
        item["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        changed = True
    if not changed:
        return
    memory["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_json_file(CLIO_KNOWLEDGE_MEMORY_FILE, memory)


def confirm_clio_claim_review(review_id: str, actor_user_id: str) -> dict[str, Any] | None:
    queue = _read_clio_claim_review_queue()
    items = queue.get("items")
    if not isinstance(items, list):
        return None
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for index, item in enumerate(items):
        if not isinstance(item, dict) or str(item.get("id")) != review_id:
            continue
        status = str(item.get("status") or "")
        if status == "confirmed_by_user":
            return item
        if status != "pending_user_review":
            return item
        applied_path = _apply_clio_note_draft_state(str(item.get("vaultFile") or ""), "confirmed")
        if not applied_path:
            return None
        history = item.get("history")
        if not isinstance(history, list):
            history = []
        history.append({"at": now, "type": "confirmed_by_user", "actorUserId": actor_user_id})
        updated = {
            **item,
            "status": "confirmed_by_user",
            "confirmedAt": now,
            "confirmedByUserId": actor_user_id,
            "history": history[-12:],
        }
        items[index] = updated
        queue["items"] = items
        _write_clio_claim_review_queue(queue)
        _update_clio_knowledge_memory_claim(review_id, draft_state="confirmed", claim_review_required=False)
        return updated
    return None


def _make_clio_note_suggestion_id(vault_file: str) -> str:
    return hashlib.sha256(str(vault_file).encode("utf-8")).hexdigest()[:12]


def _make_clio_note_suggestion_fingerprint(item: dict[str, Any]) -> str:
    payload = {
        "title": _sanitize_text(item.get("title"), 160),
        "noteAction": _sanitize_text(item.get("noteAction"), 40),
        "updateTargetPath": _sanitize_text(item.get("updateTargetPath"), 260),
        "mergeCandidatePaths": _normalize_string_list(item.get("mergeCandidatePaths"), limit=6, item_limit=260),
        "relatedNotes": _normalize_string_list(item.get("relatedNotes"), limit=8, item_limit=120),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _normalize_clio_note_suggestion(item: dict[str, Any]) -> dict[str, Any] | None:
    title = _sanitize_text(item.get("title"), 180)
    note_action = _sanitize_text(item.get("noteAction"), 40)
    vault_file = _sanitize_text(item.get("vaultFile"), 260)
    if not title or not vault_file or note_action not in {"update_candidate", "merge_candidate"}:
        return None
    fingerprint = _sanitize_text(item.get("suggestionFingerprint"), 64) or _make_clio_note_suggestion_fingerprint(item)
    return {
        "id": _make_clio_note_suggestion_id(vault_file),
        "title": title,
        "type": _sanitize_text(item.get("type"), 32),
        "vaultFile": vault_file,
        "draftState": _sanitize_text(item.get("draftState"), 32) or "draft",
        "noteAction": note_action,
        "updateTarget": _sanitize_text(item.get("updateTarget"), 160),
        "updateTargetPath": _sanitize_text(item.get("updateTargetPath"), 260),
        "mergeCandidates": _normalize_string_list(item.get("mergeCandidates"), limit=6, item_limit=120),
        "mergeCandidatePaths": _normalize_string_list(item.get("mergeCandidatePaths"), limit=6, item_limit=260),
        "projectLinks": _normalize_string_list(item.get("projectLinks"), limit=6, item_limit=120),
        "mocCandidates": _normalize_string_list(item.get("mocCandidates"), limit=6, item_limit=120),
        "suggestionScore": item.get("suggestionScore") if item.get("suggestionScore") not in {None, ""} else None,
        "suggestionReasons": _normalize_string_list(item.get("suggestionReasons"), limit=4, item_limit=140),
        "suggestionState": _sanitize_text(item.get("suggestionState"), 32) or "pending",
        "suggestionFingerprint": fingerprint,
        "dismissedAt": _sanitize_text(item.get("dismissedAt"), 64),
        "dismissedSuggestionFingerprint": _sanitize_text(item.get("dismissedSuggestionFingerprint"), 64),
        "suggestionCooldownUntil": _sanitize_text(item.get("suggestionCooldownUntil"), 64),
        "updatedAt": _sanitize_text(item.get("updatedAt"), 64),
    }


def _reactivate_clio_note_suggestion_if_due(suggestion_id: str, item: dict[str, Any]) -> bool:
    state = _sanitize_text(item.get("suggestionState"), 32) or "pending"
    if state != "dismissed":
        return False

    now = datetime.now(timezone.utc)
    cooldown_until = _parse_iso_datetime(item.get("suggestionCooldownUntil"))
    dismissed_at = _parse_iso_datetime(item.get("dismissedAt"))
    current_fingerprint = _sanitize_text(item.get("suggestionFingerprint"), 64) or _make_clio_note_suggestion_fingerprint(item)
    dismissed_fingerprint = _sanitize_text(item.get("dismissedSuggestionFingerprint"), 64)
    updated_at = _parse_iso_datetime(item.get("updatedAt"))

    if dismissed_fingerprint and current_fingerprint != dismissed_fingerprint:
        _update_clio_note_suggestion_state(
            suggestion_id,
            suggestion_state="pending",
            suggestion_fingerprint=current_fingerprint,
            dismissed_at="",
            dismissed_fingerprint="",
            cooldown_until="",
        )
        return True
    if dismissed_at and updated_at and updated_at > dismissed_at:
        _update_clio_note_suggestion_state(
            suggestion_id,
            suggestion_state="pending",
            suggestion_fingerprint=current_fingerprint,
            dismissed_at="",
            dismissed_fingerprint="",
            cooldown_until="",
        )
        return True
    if cooldown_until and now >= cooldown_until:
        _update_clio_note_suggestion_state(
            suggestion_id,
            suggestion_state="pending",
            suggestion_fingerprint=current_fingerprint,
            dismissed_at="",
            dismissed_fingerprint="",
            cooldown_until="",
        )
        return True
    return False


def _strip_frontmatter(markdown: str) -> str:
    if not markdown.startswith("---"):
        return markdown
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return markdown
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[index + 1 :]).strip()
    return markdown


def _extract_diff_candidate_lines(markdown: str, *, limit: int = 3) -> list[str]:
    body = _strip_frontmatter(markdown)
    lines: list[str] = []
    for raw in body.splitlines():
        line = _sanitize_text(raw, 180)
        if not line:
            continue
        if line.startswith("## "):
            line = line[3:].strip()
        elif line.startswith("### "):
            line = line[4:].strip()
        if len(line) < 8:
            continue
        lines.append(line)
        if len(lines) >= max(1, limit * 3):
            break
    return lines


def _build_clio_note_diff_summary(suggestion: dict[str, Any]) -> list[str]:
    draft_path = _safe_vault_path(str(suggestion.get("vaultFile") or ""))
    if not draft_path or not draft_path.is_file():
        return []

    draft_lines = _extract_diff_candidate_lines(draft_path.read_text(encoding="utf-8"), limit=4)
    if not draft_lines:
        return []

    if suggestion.get("noteAction") == "update_candidate":
        target_path = _safe_vault_path(str(suggestion.get("updateTargetPath") or ""))
        target_body = target_path.read_text(encoding="utf-8") if target_path and target_path.is_file() else ""
        target_text = _sanitize_text(_strip_frontmatter(target_body), 5000)
        summary: list[str] = []
        seen: set[str] = set()
        for line in draft_lines:
            key = line.lower()
            if key in seen:
                continue
            if target_text and line in target_text:
                continue
            summary.append(line)
            seen.add(key)
            if len(summary) >= 3:
                break
        return summary

    merge_candidates = suggestion.get("mergeCandidates") if isinstance(suggestion.get("mergeCandidates"), list) else []
    summary = []
    for item in merge_candidates[:3]:
        line = _sanitize_text(item, 120)
        if line:
            summary.append(f"연결 후보: {line}")
    return summary or draft_lines[:2]


def list_pending_clio_note_suggestions(limit: int = 8) -> list[dict[str, Any]]:
    memory = get_clio_knowledge_memory()
    suggestions: list[dict[str, Any]] = []
    for item in memory.get("recentNotes", []):
        if not isinstance(item, dict):
            continue
        suggestion_id = _make_clio_note_suggestion_id(_sanitize_text(item.get("vaultFile"), 260))
        if _reactivate_clio_note_suggestion_if_due(suggestion_id, item):
            memory = get_clio_knowledge_memory()
            for refreshed in memory.get("recentNotes", []):
                if isinstance(refreshed, dict) and _make_clio_note_suggestion_id(_sanitize_text(refreshed.get("vaultFile"), 260)) == suggestion_id:
                    item = refreshed
                    break
        suggestion = _normalize_clio_note_suggestion(item)
        if not suggestion:
            continue
        if suggestion.get("suggestionState") not in {"", "pending"}:
            continue
        suggestion["diffSummary"] = _build_clio_note_diff_summary(suggestion)
        suggestions.append(suggestion)
        if len(suggestions) >= max(1, limit):
            break
    return suggestions


def get_clio_note_suggestion(suggestion_id: str) -> dict[str, Any] | None:
    for suggestion in list_pending_clio_note_suggestions(limit=200):
        if str(suggestion.get("id")) == suggestion_id:
            return suggestion
    return None


def _update_clio_note_suggestion_state(
    suggestion_id: str,
    *,
    suggestion_state: str,
    draft_state: str | None = None,
    suggestion_fingerprint: str | None = None,
    dismissed_at: str | None = None,
    dismissed_fingerprint: str | None = None,
    cooldown_until: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any] | None:
    memory = get_clio_knowledge_memory()
    changed = False
    updated_note: dict[str, Any] | None = None
    for item in memory.get("recentNotes", []):
        if not isinstance(item, dict):
            continue
        vault_file = _sanitize_text(item.get("vaultFile"), 260)
        if _make_clio_note_suggestion_id(vault_file) != suggestion_id:
            continue
        item["suggestionState"] = suggestion_state
        if draft_state:
            item["draftState"] = draft_state
        if suggestion_fingerprint is not None:
            item["suggestionFingerprint"] = suggestion_fingerprint
        if dismissed_at is not None:
            item["dismissedAt"] = dismissed_at
        if dismissed_fingerprint is not None:
            item["dismissedSuggestionFingerprint"] = dismissed_fingerprint
        if cooldown_until is not None:
            item["suggestionCooldownUntil"] = cooldown_until
        item["updatedAt"] = _sanitize_text(updated_at, 64) or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        updated_note = dict(item)
        changed = True
        break
    if not changed:
        return None
    memory["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_json_file(CLIO_KNOWLEDGE_MEMORY_FILE, memory)
    return updated_note


def _append_note_annotation(note_path: Path, marker: str, heading: str, lines: list[str]) -> bool:
    if not note_path.is_file():
        return False
    markdown = note_path.read_text(encoding="utf-8")
    if marker in markdown:
        return True
    block = "\n".join(["", heading, marker, *lines]).rstrip() + "\n"
    note_path.write_text(markdown.rstrip() + "\n" + block, encoding="utf-8")
    return True


def apply_clio_note_suggestion(suggestion_id: str, actor_user_id: str) -> dict[str, Any] | None:
    suggestion = get_clio_note_suggestion(suggestion_id)
    if not suggestion:
        return None
    draft_path = _safe_vault_path(str(suggestion.get("vaultFile") or ""))
    if not draft_path or not draft_path.is_file():
        return None

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat().replace("+00:00", "Z")
    draft_stem = draft_path.stem
    marker = f"<!-- clio-suggestion:{suggestion_id} -->"
    applied_paths: list[str] = []

    if suggestion.get("noteAction") == "update_candidate":
        target_path = _safe_vault_path(str(suggestion.get("updateTargetPath") or ""))
        if not target_path:
            return None
        ok = _append_note_annotation(
            target_path,
            marker,
            "## Clio Suggested Update",
            [
                f"- approved_at: {now_iso}",
                f"- approved_by: {actor_user_id}",
                f"- source_draft: [[{draft_stem}]]",
                f"- suggestion_type: update_candidate",
            ],
        )
        if not ok:
            return None
        applied_paths.append(str(target_path))
    else:
        candidate_paths = []
        for raw in suggestion.get("mergeCandidatePaths") or []:
            candidate_path = _safe_vault_path(str(raw))
            if candidate_path and candidate_path.is_file():
                candidate_paths.append(candidate_path)
        if not candidate_paths:
            return None
        for candidate_path in candidate_paths[:3]:
            ok = _append_note_annotation(
                candidate_path,
                marker,
                "## Clio Related Draft",
                [
                    f"- approved_at: {now_iso}",
                    f"- approved_by: {actor_user_id}",
                    f"- linked_draft: [[{draft_stem}]]",
                    f"- suggestion_type: merge_candidate",
                ],
            )
            if ok:
                applied_paths.append(str(candidate_path))
        _append_note_annotation(
            draft_path,
            marker,
            "## Clio Approved Merge Links",
            [f"- candidate: {item}" for item in suggestion.get("mergeCandidates", [])[:3]] or ["- candidate: 없음"],
        )

    _apply_clio_note_draft_state(str(suggestion.get("vaultFile") or ""), "review")
    _update_clio_note_suggestion_state(
        suggestion_id,
        suggestion_state="approved",
        draft_state="review",
        suggestion_fingerprint=str(suggestion.get("suggestionFingerprint") or ""),
        dismissed_at="",
        dismissed_fingerprint="",
        cooldown_until="",
    )
    return {
        **suggestion,
        "appliedAt": now_iso,
        "appliedByUserId": actor_user_id,
        "appliedPaths": applied_paths,
    }


def dismiss_clio_note_suggestion(suggestion_id: str, actor_user_id: str) -> dict[str, Any] | None:
    suggestion = get_clio_note_suggestion(suggestion_id)
    if not suggestion:
        return None
    dismissed_at = datetime.now(timezone.utc)
    cooldown_until = dismissed_at + timedelta(seconds=CLIO_SUGGESTION_DISMISS_COOLDOWN_SEC)
    updated = _update_clio_note_suggestion_state(
        suggestion_id,
        suggestion_state="dismissed",
        suggestion_fingerprint=str(suggestion.get("suggestionFingerprint") or ""),
        dismissed_at=dismissed_at.isoformat().replace("+00:00", "Z"),
        dismissed_fingerprint=str(suggestion.get("suggestionFingerprint") or ""),
        cooldown_until=cooldown_until.isoformat().replace("+00:00", "Z"),
        updated_at=dismissed_at.isoformat().replace("+00:00", "Z"),
    )
    if not updated:
        return None
    return {
        **suggestion,
        "dismissedAt": dismissed_at.isoformat().replace("+00:00", "Z"),
        "suggestionCooldownUntil": cooldown_until.isoformat().replace("+00:00", "Z"),
        "dismissedByUserId": actor_user_id,
    }


def render_clio_knowledge_memory_context(memory: dict[str, Any] | None = None, *, max_chars: int = 1600) -> str | None:
    data = normalize_clio_knowledge_memory(memory if isinstance(memory, dict) else get_clio_knowledge_memory())
    lines = ["Clio knowledge memory"]
    pending_claim_reviews = len(list_pending_clio_claim_reviews(limit=200))
    pending_suggestions = len(list_pending_clio_note_suggestions(limit=200))
    if pending_claim_reviews:
        lines.append(f"- Pending claim reviews: {pending_claim_reviews}")
    if pending_suggestions:
        lines.append(f"- Pending note suggestions: {pending_suggestions}")
    projects = _normalize_string_list(data.get("projects"), limit=8, item_limit=80)
    if projects:
        lines.append(f"- Registered projects: {', '.join(projects)}")
    mocs = _normalize_string_list(data.get("mocs"), limit=8, item_limit=120)
    if mocs:
        lines.append(f"- MOC registry: {', '.join(mocs)}")
    recent_notes = data.get("recentNotes") if isinstance(data.get("recentNotes"), list) else []
    if recent_notes:
        lines.append("- Recent notes:")
        prioritized: list[dict[str, Any]] = []
        prioritized.extend(
            [
                item
                for item in recent_notes
                if isinstance(item, dict)
                and (bool(item.get("claimReviewRequired")) or _sanitize_text(item.get("suggestionState"), 24) == "pending")
            ]
        )
        prioritized.extend([item for item in recent_notes if isinstance(item, dict) and item not in prioritized])
        for item in prioritized[:4]:
            if not isinstance(item, dict):
                continue
            title = _sanitize_text(item.get("title"), 90)
            note_type = _sanitize_text(item.get("type"), 24)
            draft_state = _sanitize_text(item.get("draftState"), 24)
            note_action = _sanitize_text(item.get("noteAction"), 32)
            suggestion_state = _sanitize_text(item.get("suggestionState"), 24)
            suggestion_score = item.get("suggestionScore")
            suffix = f" action={note_action}" if note_action else ""
            if suggestion_state:
                suffix += f" suggestion={suggestion_state}"
            if isinstance(suggestion_score, (int, float)):
                suffix += f" score={float(suggestion_score):.2f}"
            lines.append(f"  - {title} [{note_type}] state={draft_state or 'draft'}{suffix}")
    dedupe_candidates = data.get("dedupeCandidates") if isinstance(data.get("dedupeCandidates"), list) else []
    if dedupe_candidates:
        lines.append("- Dedupe candidates:")
        for item in dedupe_candidates[:2]:
            if not isinstance(item, dict):
                continue
            title = _sanitize_text(item.get("title"), 90)
            related = _normalize_string_list(item.get("relatedNotes"), limit=2, item_limit=80)
            lines.append(f"  - {title}" + (f" -> {', '.join(related)}" if related else ""))
    text = "\n".join(lines).strip()
    return text[: max_chars - 1].rstrip() + "…" if len(text) > max_chars else text or None
