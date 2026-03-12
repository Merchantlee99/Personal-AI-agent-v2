from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .orch_clio_common import safe_vault_path, update_frontmatter_scalar


def default_clio_claim_review_queue() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": [],
    }


def normalize_clio_claim_review_queue(
    payload: dict[str, Any] | None,
    *,
    sanitize_text: Callable[[Any, int], str],
    normalize_string_list: Callable[..., list[str]],
) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    queue = default_clio_claim_review_queue()
    queue["schemaVersion"] = int(raw.get("schemaVersion", 1) or 1)
    queue["updatedAt"] = sanitize_text(raw.get("updatedAt"), 64) or queue["updatedAt"]

    items: list[dict[str, Any]] = []
    for item in raw.get("items") or []:
        if not isinstance(item, dict):
            continue
        review_id = sanitize_text(item.get("id"), 32)
        title = sanitize_text(item.get("title"), 180)
        topic_key = sanitize_text(item.get("topicKey"), 120)
        vault_file = sanitize_text(item.get("vaultFile"), 260)
        if not review_id or not title or not topic_key or not vault_file:
            continue
        items.append(
            {
                "id": review_id,
                "status": sanitize_text(item.get("status"), 40) or "pending_user_review",
                "title": title,
                "topicKey": topic_key,
                "vaultFile": vault_file,
                "sourceUrls": normalize_string_list(item.get("sourceUrls"), limit=8, item_limit=240),
                "projectLinks": normalize_string_list(item.get("projectLinks"), limit=6, item_limit=120),
                "mocCandidates": normalize_string_list(item.get("mocCandidates"), limit=6, item_limit=120),
                "requestedAt": sanitize_text(item.get("requestedAt"), 64),
                "confirmedAt": sanitize_text(item.get("confirmedAt"), 64),
                "confirmedByUserId": sanitize_text(item.get("confirmedByUserId"), 80),
                "history": item.get("history") if isinstance(item.get("history"), list) else [],
            }
        )
        if len(items) >= 200:
            break
    queue["items"] = items
    return queue


def read_clio_claim_review_queue(
    *,
    queue_file: Path,
    read_json_file: Callable[[Path, Any], Any],
    sanitize_text: Callable[[Any, int], str],
    normalize_string_list: Callable[..., list[str]],
) -> dict[str, Any]:
    payload = read_json_file(queue_file, default_clio_claim_review_queue())
    return normalize_clio_claim_review_queue(
        payload if isinstance(payload, dict) else None,
        sanitize_text=sanitize_text,
        normalize_string_list=normalize_string_list,
    )


def write_clio_claim_review_queue(
    *,
    queue_file: Path,
    queue: dict[str, Any],
    write_json_file: Callable[[Path, Any], None],
) -> None:
    queue["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    write_json_file(queue_file, queue)


def get_clio_claim_review(
    *,
    queue_file: Path,
    review_id: str,
    read_json_file: Callable[[Path, Any], Any],
    sanitize_text: Callable[[Any, int], str],
    normalize_string_list: Callable[..., list[str]],
) -> dict[str, Any] | None:
    review_id = sanitize_text(review_id, 32)
    if not review_id:
        return None
    for item in read_clio_claim_review_queue(
        queue_file=queue_file,
        read_json_file=read_json_file,
        sanitize_text=sanitize_text,
        normalize_string_list=normalize_string_list,
    ).get("items", []):
        if isinstance(item, dict) and item.get("id") == review_id:
            return item
    return None


def list_pending_clio_claim_reviews(
    *,
    queue_file: Path,
    limit: int,
    read_json_file: Callable[[Path, Any], Any],
    sanitize_text: Callable[[Any, int], str],
    normalize_string_list: Callable[..., list[str]],
) -> list[dict[str, Any]]:
    items = [
        item
        for item in read_clio_claim_review_queue(
            queue_file=queue_file,
            read_json_file=read_json_file,
            sanitize_text=sanitize_text,
            normalize_string_list=normalize_string_list,
        ).get("items", [])
        if isinstance(item, dict) and str(item.get("status")) == "pending_user_review"
    ]
    items.sort(key=lambda row: str(row.get("requestedAt", "")), reverse=True)
    return items[: max(1, limit)]


def default_clio_alert_state() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "claimReviewAlerts": {},
        "noteSuggestionAlerts": {},
    }


def read_clio_alert_state(
    *,
    alert_state_file: Path,
    read_json_file: Callable[[Path, Any], Any],
    sanitize_text: Callable[[Any, int], str],
) -> dict[str, Any]:
    payload = read_json_file(alert_state_file, default_clio_alert_state())
    if not isinstance(payload, dict):
        return default_clio_alert_state()
    normalized = default_clio_alert_state()
    normalized["updatedAt"] = sanitize_text(payload.get("updatedAt"), 64) or normalized["updatedAt"]
    for key in ("claimReviewAlerts", "noteSuggestionAlerts"):
        source = payload.get(key)
        if not isinstance(source, dict):
            continue
        normalized[key] = {
            sanitize_text(item_key, 64): {
                "fingerprint": sanitize_text(item_value.get("fingerprint"), 64),
                "sentAt": sanitize_text(item_value.get("sentAt"), 64),
            }
            for item_key, item_value in source.items()
            if sanitize_text(item_key, 64) and isinstance(item_value, dict)
        }
    return normalized


def write_clio_alert_state(
    *,
    alert_state_file: Path,
    state: dict[str, Any],
    write_json_file: Callable[[Path, Any], None],
) -> None:
    state["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    write_json_file(alert_state_file, state)


def apply_clio_note_draft_state(
    *,
    root: Path,
    vault_file: str,
    sanitize_text: Callable[[Any, int], str],
) -> str | None:
    note_path = safe_vault_path(root, vault_file, sanitize_text)
    if not note_path or not note_path.is_file():
        return None
    markdown = note_path.read_text(encoding="utf-8")
    updated = update_frontmatter_scalar(markdown, "draft_state", "confirmed")
    updated = update_frontmatter_scalar(updated, "updated", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    note_path.write_text(updated, encoding="utf-8")
    return str(note_path)


def update_clio_knowledge_memory_claim(
    *,
    clio_memory_file: Path,
    get_clio_knowledge_memory: Callable[[], dict[str, Any]],
    write_json_file: Callable[[Path, Any], None],
    review_id: str,
    draft_state: str,
    claim_review_required: bool,
) -> None:
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
    write_json_file(clio_memory_file, memory)


def confirm_clio_claim_review(
    *,
    root: Path,
    queue_file: Path,
    clio_memory_file: Path,
    review_id: str,
    actor_user_id: str,
    read_json_file: Callable[[Path, Any], Any],
    write_json_file: Callable[[Path, Any], None],
    sanitize_text: Callable[[Any, int], str],
    normalize_string_list: Callable[..., list[str]],
    get_clio_knowledge_memory: Callable[[], dict[str, Any]],
) -> dict[str, Any] | None:
    queue = read_clio_claim_review_queue(
        queue_file=queue_file,
        read_json_file=read_json_file,
        sanitize_text=sanitize_text,
        normalize_string_list=normalize_string_list,
    )
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
        applied_path = apply_clio_note_draft_state(
            root=root,
            vault_file=str(item.get("vaultFile") or ""),
            sanitize_text=sanitize_text,
        )
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
        write_clio_claim_review_queue(queue_file=queue_file, queue=queue, write_json_file=write_json_file)
        update_clio_knowledge_memory_claim(
            clio_memory_file=clio_memory_file,
            get_clio_knowledge_memory=get_clio_knowledge_memory,
            write_json_file=write_json_file,
            review_id=review_id,
            draft_state="confirmed",
            claim_review_required=False,
        )
        return updated
    return None
