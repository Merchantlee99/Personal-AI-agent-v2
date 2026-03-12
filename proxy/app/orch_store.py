from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .orch_approval import (
    approval_is_pending as _approval_is_pending,
    create_approval_request as _create_approval_request,
    get_approval_queue_stats as _get_approval_queue_stats,
    get_approval_request as _get_approval_request,
    list_pending_approvals as _list_pending_approvals,
    mark_approval_executed as _mark_approval_executed,
    reject_approval_request as _reject_approval_request,
    approve_stage_one as _approve_stage_one,
)
from .orch_clio_state import (
    append_note_annotation as _append_note_annotation_impl,
    build_clio_note_diff_summary as _build_clio_note_diff_summary_impl,
    confirm_clio_claim_review as _confirm_clio_claim_review_impl,
    default_clio_alert_state as _default_clio_alert_state,
    default_clio_claim_review_queue as _default_clio_claim_review_queue,
    get_clio_claim_review as _get_clio_claim_review_impl,
    list_pending_clio_claim_reviews as _list_pending_clio_claim_reviews_impl,
    make_clio_note_suggestion_fingerprint as _make_clio_note_suggestion_fingerprint_impl,
    make_clio_note_suggestion_id as _make_clio_note_suggestion_id_impl,
    normalize_clio_claim_review_queue as _normalize_clio_claim_review_queue,
    normalize_clio_note_suggestion as _normalize_clio_note_suggestion_impl,
    reactivate_clio_note_suggestion_if_due as _reactivate_clio_note_suggestion_if_due_impl,
    read_clio_alert_state as _read_clio_alert_state_impl,
    render_clio_knowledge_memory_context as _render_clio_knowledge_memory_context_impl,
    safe_vault_path as _safe_vault_path_impl,
    update_clio_note_suggestion_state as _update_clio_note_suggestion_state_impl,
    write_clio_alert_state as _write_clio_alert_state_impl,
)
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
    sanitize_text as _sanitize_text,
    set_cooldown,
    upsert_hermes_evidence_memory,
    write_json_file as _write_json_file,
)
from .pipeline_contract import normalize_approval_request_artifact

APPROVAL_QUEUE_FILE = MEMORY_DIR / "approval_queue.json"
CLIO_CLAIM_REVIEW_QUEUE_FILE = MEMORY_DIR / "clio_claim_review_queue.json"
CLIO_ALERT_STATE_FILE = MEMORY_DIR / "clio_alert_state.json"
APPROVAL_TTL_SEC = max(60, int(float(os.getenv("TELEGRAM_APPROVAL_TTL_SEC", "300") or "300")))
APPROVAL_RETENTION_HOURS = max(1, int(float(os.getenv("TELEGRAM_APPROVAL_RETENTION_HOURS", "72") or "72")))
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
    return _create_approval_request(
        approval_queue_file=APPROVAL_QUEUE_FILE,
        action=action,
        event_id=event_id,
        event_title=event_title,
        topic_key=topic_key,
        chat_id=chat_id,
        requested_by_user_id=requested_by_user_id,
        payload=payload,
        approval_ttl_sec=APPROVAL_TTL_SEC,
        required_steps=_approval_required_steps(),
        read_json_file=_read_json_file,
        write_json_file=_write_json_file,
        normalize_approval_request_artifact=normalize_approval_request_artifact,
        retention_hours=APPROVAL_RETENTION_HOURS,
    )


def get_approval_request(approval_id: str) -> dict[str, Any] | None:
    return _get_approval_request(
        approval_queue_file=APPROVAL_QUEUE_FILE,
        approval_id=approval_id,
        read_json_file=_read_json_file,
        retention_hours=APPROVAL_RETENTION_HOURS,
    )


def approve_stage_one(approval_id: str, actor_user_id: str) -> dict[str, Any] | None:
    return _approve_stage_one(
        approval_queue_file=APPROVAL_QUEUE_FILE,
        approval_id=approval_id,
        actor_user_id=actor_user_id,
        read_json_file=_read_json_file,
        write_json_file=_write_json_file,
        retention_hours=APPROVAL_RETENTION_HOURS,
    )


def reject_approval_request(approval_id: str, actor_user_id: str) -> dict[str, Any] | None:
    return _reject_approval_request(
        approval_queue_file=APPROVAL_QUEUE_FILE,
        approval_id=approval_id,
        actor_user_id=actor_user_id,
        read_json_file=_read_json_file,
        write_json_file=_write_json_file,
        retention_hours=APPROVAL_RETENTION_HOURS,
    )


def mark_approval_executed(approval_id: str, actor_user_id: str) -> dict[str, Any] | None:
    return _mark_approval_executed(
        approval_queue_file=APPROVAL_QUEUE_FILE,
        approval_id=approval_id,
        actor_user_id=actor_user_id,
        read_json_file=_read_json_file,
        write_json_file=_write_json_file,
        retention_hours=APPROVAL_RETENTION_HOURS,
    )


def list_pending_approvals(limit: int = 60) -> list[dict[str, Any]]:
    return _list_pending_approvals(
        approval_queue_file=APPROVAL_QUEUE_FILE,
        limit=limit,
        read_json_file=_read_json_file,
        retention_hours=APPROVAL_RETENTION_HOURS,
    )


def get_approval_queue_stats() -> dict[str, Any]:
    return _get_approval_queue_stats(
        approval_queue_file=APPROVAL_QUEUE_FILE,
        read_json_file=_read_json_file,
        retention_hours=APPROVAL_RETENTION_HOURS,
    )


def normalize_clio_claim_review_queue(payload: dict[str, Any] | None) -> dict[str, Any]:
    return _normalize_clio_claim_review_queue(
        payload,
        sanitize_text=_sanitize_text,
        normalize_string_list=_normalize_string_list,
    )


def get_clio_claim_review(review_id: str) -> dict[str, Any] | None:
    return _get_clio_claim_review_impl(
        queue_file=CLIO_CLAIM_REVIEW_QUEUE_FILE,
        review_id=review_id,
        read_json_file=_read_json_file,
        sanitize_text=_sanitize_text,
        normalize_string_list=_normalize_string_list,
    )


def list_pending_clio_claim_reviews(limit: int = 8) -> list[dict[str, Any]]:
    return _list_pending_clio_claim_reviews_impl(
        queue_file=CLIO_CLAIM_REVIEW_QUEUE_FILE,
        limit=limit,
        read_json_file=_read_json_file,
        sanitize_text=_sanitize_text,
        normalize_string_list=_normalize_string_list,
    )


def _read_clio_alert_state() -> dict[str, Any]:
    return _read_clio_alert_state_impl(
        alert_state_file=CLIO_ALERT_STATE_FILE,
        read_json_file=_read_json_file,
        sanitize_text=_sanitize_text,
    )


def _write_clio_alert_state(state: dict[str, Any]) -> None:
    _write_clio_alert_state_impl(
        alert_state_file=CLIO_ALERT_STATE_FILE,
        state=state,
        write_json_file=_write_json_file,
    )


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


def _safe_vault_path(relative_or_absolute: str) -> Path | None:
    return _safe_vault_path_impl(ROOT, relative_or_absolute, _sanitize_text)


def _apply_clio_note_draft_state(vault_file: str, draft_state: str) -> str | None:
    note_path = _safe_vault_path(vault_file)
    if not note_path or not note_path.is_file():
        return None
    markdown = note_path.read_text(encoding="utf-8")
    from .orch_clio_state import update_frontmatter_scalar

    updated = update_frontmatter_scalar(markdown, "draft_state", draft_state)
    updated = update_frontmatter_scalar(updated, "updated", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    note_path.write_text(updated, encoding="utf-8")
    return str(note_path)


def _update_clio_knowledge_memory_claim(review_id: str, *, draft_state: str, claim_review_required: bool) -> None:
    from .orch_clio_state import update_clio_knowledge_memory_claim

    update_clio_knowledge_memory_claim(
        clio_memory_file=CLIO_KNOWLEDGE_MEMORY_FILE,
        get_clio_knowledge_memory=get_clio_knowledge_memory,
        write_json_file=_write_json_file,
        review_id=review_id,
        draft_state=draft_state,
        claim_review_required=claim_review_required,
    )


def confirm_clio_claim_review(review_id: str, actor_user_id: str) -> dict[str, Any] | None:
    return _confirm_clio_claim_review_impl(
        root=ROOT,
        queue_file=CLIO_CLAIM_REVIEW_QUEUE_FILE,
        clio_memory_file=CLIO_KNOWLEDGE_MEMORY_FILE,
        review_id=review_id,
        actor_user_id=actor_user_id,
        read_json_file=_read_json_file,
        write_json_file=_write_json_file,
        sanitize_text=_sanitize_text,
        normalize_string_list=_normalize_string_list,
        get_clio_knowledge_memory=get_clio_knowledge_memory,
    )


def _make_clio_note_suggestion_id(vault_file: str) -> str:
    return _make_clio_note_suggestion_id_impl(vault_file)


def _make_clio_note_suggestion_fingerprint(item: dict[str, Any]) -> str:
    return _make_clio_note_suggestion_fingerprint_impl(
        item,
        sanitize_text=_sanitize_text,
        normalize_string_list=_normalize_string_list,
    )


def _normalize_clio_note_suggestion(item: dict[str, Any]) -> dict[str, Any] | None:
    return _normalize_clio_note_suggestion_impl(
        item,
        sanitize_text=_sanitize_text,
        normalize_string_list=_normalize_string_list,
    )


def _reactivate_clio_note_suggestion_if_due(suggestion_id: str, item: dict[str, Any]) -> bool:
    return _reactivate_clio_note_suggestion_if_due_impl(
        suggestion_id=suggestion_id,
        item=item,
        parse_iso_datetime=_parse_iso_datetime,
        sanitize_text=_sanitize_text,
        make_fingerprint=_make_clio_note_suggestion_fingerprint,
        update_state=_update_clio_note_suggestion_state,
    )


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
                if isinstance(refreshed, dict) and _make_clio_note_suggestion_id(
                    _sanitize_text(refreshed.get("vaultFile"), 260)
                ) == suggestion_id:
                    item = refreshed
                    break
        suggestion = _normalize_clio_note_suggestion(item)
        if not suggestion:
            continue
        if suggestion.get("suggestionState") not in {"", "pending"}:
            continue
        suggestion["diffSummary"] = _build_clio_note_diff_summary_impl(
            suggestion,
            root=ROOT,
            sanitize_text=_sanitize_text,
        )
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
    return _update_clio_note_suggestion_state_impl(
        clio_memory_file=CLIO_KNOWLEDGE_MEMORY_FILE,
        get_clio_knowledge_memory=get_clio_knowledge_memory,
        write_json_file=_write_json_file,
        suggestion_id=suggestion_id,
        suggestion_state=suggestion_state,
        draft_state=draft_state,
        suggestion_fingerprint=suggestion_fingerprint,
        dismissed_at=dismissed_at,
        dismissed_fingerprint=dismissed_fingerprint,
        cooldown_until=cooldown_until,
        updated_at=updated_at,
        sanitize_text=_sanitize_text,
    )


def _append_note_annotation(note_path: Path, marker: str, heading: str, lines: list[str]) -> bool:
    return _append_note_annotation_impl(note_path, marker, heading, lines)


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
    return _render_clio_knowledge_memory_context_impl(
        memory=memory if isinstance(memory, dict) else get_clio_knowledge_memory(),
        pending_claim_reviews=len(list_pending_clio_claim_reviews(limit=200)),
        pending_suggestions=len(list_pending_clio_note_suggestions(limit=200)),
        sanitize_text=_sanitize_text,
        normalize_clio_knowledge_memory=normalize_clio_knowledge_memory,
        normalize_string_list=_normalize_string_list,
        max_chars=max_chars,
    )
