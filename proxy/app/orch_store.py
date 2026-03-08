from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .pipeline_contract import normalize_approval_request_artifact

ROOT = Path((os.getenv("SHARED_ROOT_PATH") or "/app/shared_data").strip() or "/app/shared_data")
MEMORY_DIR = ROOT / "shared_memory"
EVENTS_FILE = MEMORY_DIR / "agent_events.json"
COOLDOWN_FILE = MEMORY_DIR / "topic_cooldowns.json"
DIGEST_FILE = MEMORY_DIR / "digest_queue.json"
TELEGRAM_CHAT_HISTORY_FILE = MEMORY_DIR / "telegram_chat_history.json"
APPROVAL_QUEUE_FILE = MEMORY_DIR / "approval_queue.json"
MEMORY_MARKDOWN_FILE = MEMORY_DIR / "memory.md"
MINERVA_WORKING_MEMORY_FILE = MEMORY_DIR / "minerva_working_memory.json"
CLIO_KNOWLEDGE_MEMORY_FILE = MEMORY_DIR / "clio_knowledge_memory.json"
CLIO_CLAIM_REVIEW_QUEUE_FILE = MEMORY_DIR / "clio_claim_review_queue.json"
HERMES_EVIDENCE_MEMORY_FILE = MEMORY_DIR / "hermes_evidence_memory.json"

MEMORY_MARKDOWN_MAX_BYTES = max(32000, int(float(os.getenv("MEMORY_MD_MAX_BYTES", "280000") or "280000")))
MEMORY_MARKDOWN_HEADER = (
    "# NanoClaw Runtime Memory\n\n"
    "- Purpose: shared runtime log for Minerva/Clio/Hermes orchestration.\n"
    "- Source: generated automatically from event + telegram chat paths.\n"
    "- Retention: auto-rotated when file exceeds size limit.\n\n"
    "## Timeline\n\n"
)

MEMORY_SKIP_TAGS = {
    item.strip().lower()
    for item in (os.getenv("MEMORY_SKIP_TAGS") or "verification,rehearsal,test,smoke").split(",")
    if item.strip()
}
APPROVAL_TTL_SEC = max(60, int(float(os.getenv("TELEGRAM_APPROVAL_TTL_SEC", "300") or "300")))
APPROVAL_RETENTION_HOURS = max(
    1, int(float(os.getenv("TELEGRAM_APPROVAL_RETENTION_HOURS", "72") or "72"))
)


def _ensure_memory_dir() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _read_json_file(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return fallback


def _write_json_file(path: Path, payload: Any) -> None:
    _ensure_memory_dir()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _single_line(value: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", str(value or "")).replace("|", "\\|").strip()
    if not compact:
        return "-"
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(8, limit - 1)].rstrip()}…"


def _ensure_memory_md() -> None:
    _ensure_memory_dir()
    if MEMORY_MARKDOWN_FILE.is_file():
        return
    MEMORY_MARKDOWN_FILE.write_text(MEMORY_MARKDOWN_HEADER, encoding="utf-8")


def _rotate_memory_md(content: str) -> str:
    encoded = content.encode("utf-8")
    if len(encoded) <= MEMORY_MARKDOWN_MAX_BYTES:
        return content
    tail_bytes = encoded[-(MEMORY_MARKDOWN_MAX_BYTES - len(MEMORY_MARKDOWN_HEADER.encode("utf-8")) + 256) :]
    tail = tail_bytes.decode("utf-8", errors="ignore")
    marker = tail.find("### ")
    if marker >= 0:
        tail = tail[marker:]
    return f"{MEMORY_MARKDOWN_HEADER}{tail.lstrip()}"


def _append_memory_block(lines: list[str]) -> None:
    _ensure_memory_md()
    block = "\n".join(lines).rstrip() + "\n\n"
    try:
        current = MEMORY_MARKDOWN_FILE.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        current = MEMORY_MARKDOWN_HEADER
    next_content = _rotate_memory_md(f"{current.rstrip()}\n\n{block}")
    MEMORY_MARKDOWN_FILE.write_text(next_content, encoding="utf-8")


def create_event_id() -> str:
    return str(uuid.uuid4())


def make_dedupe_key(topic_key: str, summary: str) -> str:
    return hashlib.sha256(f"{topic_key}::{summary}".encode("utf-8")).hexdigest()[:20]


def _should_skip_event_for_memory(event: dict[str, Any]) -> bool:
    tags = {str(item).strip().lower() for item in event.get("tags", []) if str(item).strip()}
    if any(tag in MEMORY_SKIP_TAGS for tag in tags):
        return True

    haystack = f"{event.get('title','')}\n{event.get('topicKey','')}\n{event.get('summary','')}".lower()
    if re.search(r"\b(memory[-\s]?md|verification|rehearsal|smoke(\s|-)?test|healthcheck|heartbeat)\b", haystack):
        return True

    source_refs = event.get("sourceRefs") or []
    if (
        event.get("agentId") == "hermes"
        and len(source_refs) == 0
        and re.match(r"^total=\d+,\s*hot=\d+,\s*insight=\d+,\s*monitor=\d+$", str(event.get("summary", "")).strip(), re.I)
    ):
        return True
    return False


def _append_event_to_memory_md(event: dict[str, Any]) -> None:
    if _should_skip_event_for_memory(event):
        return

    lines: list[str] = [
        f"### {event.get('createdAt')} [{event.get('agentId')}] {_single_line(str(event.get('title', '')), 90)}",
        f"- event_id: {event.get('eventId')}",
        f"- topic: {_single_line(str(event.get('topicKey', '')), 84)}",
        f"- priority_confidence: {event.get('priority')}/{event.get('confidence')}",
        f"- tags: {_single_line(', '.join(event.get('tags') or []), 160)}",
        f"- summary: {_single_line(str(event.get('summary', '')), 220)}",
    ]
    for source in (event.get("sourceRefs") or [])[:4]:
        title = _single_line(str(source.get("title", "")), 84)
        url = _single_line(str(source.get("url", "")), 140)
        lines.append(f"- source: {title} | {url}")
    _append_memory_block(lines)


def append_agent_event(event: dict[str, Any]) -> None:
    events = _read_json_file(EVENTS_FILE, [])
    if not isinstance(events, list):
        events = []
    events.append(event)
    _write_json_file(EVENTS_FILE, events[-3000:])
    try:
        upsert_hermes_evidence_memory(event)
    except Exception:  # noqa: BLE001
        pass
    try:
        _append_event_to_memory_md(event)
    except Exception:  # noqa: BLE001
        pass


def list_agent_events() -> list[dict[str, Any]]:
    payload = _read_json_file(EVENTS_FILE, [])
    return payload if isinstance(payload, list) else []


def find_event_by_id(event_id: str) -> dict[str, Any] | None:
    for event in list_agent_events():
        if str(event.get("eventId")) == event_id:
            return event
    return None


def get_cooldown(topic_key: str) -> str | None:
    cooldowns = _read_json_file(COOLDOWN_FILE, {})
    if not isinstance(cooldowns, dict):
        return None
    value = cooldowns.get(topic_key)
    return str(value) if isinstance(value, str) and value.strip() else None


def set_cooldown(topic_key: str, until_iso: str) -> None:
    cooldowns = _read_json_file(COOLDOWN_FILE, {})
    if not isinstance(cooldowns, dict):
        cooldowns = {}
    cooldowns[topic_key] = until_iso
    _write_json_file(COOLDOWN_FILE, cooldowns)


def push_digest_item(slot: str, event: dict[str, Any]) -> None:
    queue = _read_json_file(DIGEST_FILE, {})
    if not isinstance(queue, dict):
        queue = {}
    bucket = queue.get(slot) if isinstance(queue.get(slot), dict) else None
    items = list(bucket.get("items", [])) if bucket else []
    items.append(event)
    queue[slot] = {
        "slot": slot,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": items[-200:],
    }
    _write_json_file(DIGEST_FILE, queue)


def create_inbox_task(
    *,
    target_agent_id: str,
    reason: str,
    topic_key: str,
    title: str,
    summary: str,
    source_refs: list[dict[str, str]],
) -> dict[str, str]:
    inbox_dir = ROOT / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    stamp = now.replace(":", "-").replace(".", "-")
    file_name = f"{stamp}-{target_agent_id}-{uuid.uuid4().hex[:8]}.json"
    target_path = inbox_dir / file_name

    lines = [
        f"[trigger] {reason}",
        f"[topic] {topic_key}",
        f"[title] {title}",
        "",
        summary,
        "",
        "[sources]",
    ] + [f"- {ref.get('title', '')}: {ref.get('url', '')}" for ref in source_refs]

    payload = {
        "schema_version": 1,
        "agent_id": target_agent_id,
        "source": "telegram-inline-action",
        "message": "\n".join(lines),
        "triggered_at": now,
    }
    target_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"inboxFile": file_name, "path": str(target_path)}


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


def _safe_shared_path(relative_or_absolute: str) -> Path | None:
    raw = _sanitize_text(relative_or_absolute, 260)
    if not raw:
        return None
    candidate = Path(raw)
    resolved = candidate.resolve() if candidate.is_absolute() else (ROOT / candidate).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
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
    note_path = _safe_shared_path(vault_file)
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


def _normalize_clio_note_suggestion(item: dict[str, Any]) -> dict[str, Any] | None:
    title = _sanitize_text(item.get("title"), 180)
    note_action = _sanitize_text(item.get("noteAction"), 40)
    vault_file = _sanitize_text(item.get("vaultFile"), 260)
    if not title or not vault_file or note_action not in {"update_candidate", "merge_candidate"}:
        return None
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
        "suggestionState": _sanitize_text(item.get("suggestionState"), 32) or "pending",
        "updatedAt": _sanitize_text(item.get("updatedAt"), 64),
    }


def list_pending_clio_note_suggestions(limit: int = 8) -> list[dict[str, Any]]:
    memory = get_clio_knowledge_memory()
    suggestions: list[dict[str, Any]] = []
    for item in memory.get("recentNotes", []):
        if not isinstance(item, dict):
            continue
        suggestion = _normalize_clio_note_suggestion(item)
        if not suggestion:
            continue
        if suggestion.get("suggestionState") not in {"", "pending"}:
            continue
        suggestions.append(suggestion)
        if len(suggestions) >= max(1, limit):
            break
    return suggestions


def get_clio_note_suggestion(suggestion_id: str) -> dict[str, Any] | None:
    for suggestion in list_pending_clio_note_suggestions(limit=200):
        if str(suggestion.get("id")) == suggestion_id:
            return suggestion
    return None


def _update_clio_note_suggestion_state(suggestion_id: str, *, suggestion_state: str, draft_state: str | None = None) -> dict[str, Any] | None:
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
        item["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
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
    draft_path = _safe_shared_path(str(suggestion.get("vaultFile") or ""))
    if not draft_path or not draft_path.is_file():
        return None

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat().replace("+00:00", "Z")
    draft_stem = draft_path.stem
    marker = f"<!-- clio-suggestion:{suggestion_id} -->"
    applied_paths: list[str] = []

    if suggestion.get("noteAction") == "update_candidate":
        target_path = _safe_shared_path(str(suggestion.get("updateTargetPath") or ""))
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
            candidate_path = _safe_shared_path(str(raw))
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
    _update_clio_note_suggestion_state(suggestion_id, suggestion_state="approved", draft_state="review")
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
    updated = _update_clio_note_suggestion_state(suggestion_id, suggestion_state="dismissed")
    if not updated:
        return None
    return {
        **suggestion,
        "dismissedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "dismissedByUserId": actor_user_id,
    }


def get_telegram_chat_history(chat_id: str, limit: int = 12) -> list[dict[str, str]]:
    payload = _read_json_file(TELEGRAM_CHAT_HISTORY_FILE, {})
    if not isinstance(payload, dict):
        return []
    rows = payload.get(chat_id)
    if not isinstance(rows, list):
        return []
    normalized = []
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role", ""))
        text = str(entry.get("text", "")).strip()
        if role not in {"user", "assistant"} or not text:
            continue
        normalized.append({"role": role, "text": text, "at": str(entry.get("at", ""))})
    return normalized[-max(1, limit) :]


def _append_telegram_turn_to_memory(chat_id: str, user_text: str, assistant_text: str, at: str) -> None:
    _append_memory_block(
        [
            f"### {at} [telegram][chat:{_single_line(chat_id, 48)}]",
            f"- user: {_single_line(user_text, 180)}",
            f"- minerva: {_single_line(assistant_text, 220)}",
        ]
    )


def append_telegram_chat_history(
    *,
    chat_id: str,
    user_text: str,
    assistant_text: str,
    max_entries: int = 24,
) -> None:
    max_entries = max(4, max_entries)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = _read_json_file(TELEGRAM_CHAT_HISTORY_FILE, {})
    if not isinstance(payload, dict):
        payload = {}
    current = payload.get(chat_id) if isinstance(payload.get(chat_id), list) else []
    current.append({"role": "user", "text": user_text, "at": now})
    current.append({"role": "assistant", "text": assistant_text, "at": now})
    payload[chat_id] = current[-max_entries:]
    _write_json_file(TELEGRAM_CHAT_HISTORY_FILE, payload)
    try:
        _append_telegram_turn_to_memory(chat_id, user_text, assistant_text, now)
    except Exception:  # noqa: BLE001
        pass


def clear_telegram_chat_history(chat_id: str) -> None:
    payload = _read_json_file(TELEGRAM_CHAT_HISTORY_FILE, {})
    if not isinstance(payload, dict):
        return
    if chat_id in payload:
        del payload[chat_id]
        _write_json_file(TELEGRAM_CHAT_HISTORY_FILE, payload)


def get_runtime_memory_markdown_path() -> str:
    _ensure_memory_md()
    return str(MEMORY_MARKDOWN_FILE)


def _default_minerva_working_memory() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "identity": {},
        "careerTrajectory": {},
        "positioning": {},
        "activeProjects": [],
        "credentials": [],
        "workingStyle": {},
        "currentGaps": [],
        "watchItems": [],
        "openLoops": [],
    }


def _sanitize_text(value: Any, limit: int = 240) -> str:
    compact = re.sub(r"\s+", " ", str(value or "")).strip()
    if not compact:
        return ""
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(16, limit - 1)].rstrip()}…"


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def _normalize_string_list(values: Any, *, limit: int = 12, item_limit: int = 180) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        token = _sanitize_text(item, item_limit)
        key = token.lower()
        if not token or key in seen:
            continue
        normalized.append(token)
        seen.add(key)
        if len(normalized) >= max(1, limit):
            break
    return normalized


def _normalize_project_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    name = _sanitize_text(entry.get("name"), 80)
    if not name:
        return None
    project = {
        "name": name,
        "role": _sanitize_text(entry.get("role"), 80),
        "stage": _sanitize_text(entry.get("stage"), 80),
        "priority": _sanitize_text(entry.get("priority"), 32),
        "objective": _sanitize_text(entry.get("objective"), 220),
        "facts": _normalize_string_list(entry.get("facts"), limit=6, item_limit=180),
    }
    return {key: value for key, value in project.items() if _has_meaningful_value(value)}


def normalize_minerva_working_memory(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    memory = _default_minerva_working_memory()
    memory["schemaVersion"] = int(raw.get("schemaVersion", 1) or 1)
    memory["updatedAt"] = _sanitize_text(
        raw.get("updatedAt") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        64,
    )

    identity_raw = raw.get("identity") if isinstance(raw.get("identity"), dict) else {}
    memory["identity"] = {
        key: value
        for key, value in {
            "preferredName": _sanitize_text(identity_raw.get("preferredName"), 48),
            "legalName": _sanitize_text(identity_raw.get("legalName"), 48),
            "locale": _sanitize_text(identity_raw.get("locale"), 24),
            "timezone": _sanitize_text(identity_raw.get("timezone"), 48),
            "careerStage": _sanitize_text(identity_raw.get("careerStage"), 80),
        }.items()
        if value
    }

    trajectory_raw = raw.get("careerTrajectory") if isinstance(raw.get("careerTrajectory"), dict) else {}
    memory["careerTrajectory"] = {
        key: value
        for key, value in {
            "shortTerm": _sanitize_text(trajectory_raw.get("shortTerm"), 160),
            "midTerm": _sanitize_text(trajectory_raw.get("midTerm"), 160),
            "longTerm": _sanitize_text(trajectory_raw.get("longTerm"), 160),
            "educationPlan": _normalize_string_list(trajectory_raw.get("educationPlan"), limit=4, item_limit=120),
        }.items()
        if _has_meaningful_value(value)
    }

    positioning_raw = raw.get("positioning") if isinstance(raw.get("positioning"), dict) else {}
    memory["positioning"] = {
        key: value
        for key, value in {
            "thesis": _sanitize_text(positioning_raw.get("thesis"), 120),
            "targetRole": _sanitize_text(positioning_raw.get("targetRole"), 120),
            "strengths": _normalize_string_list(positioning_raw.get("strengths"), limit=8, item_limit=120),
            "targetCompanies": _normalize_string_list(positioning_raw.get("targetCompanies"), limit=8, item_limit=80),
        }.items()
        if _has_meaningful_value(value)
    }

    projects = []
    for item in raw.get("activeProjects") or []:
        project = _normalize_project_entry(item)
        if project:
            projects.append(project)
        if len(projects) >= 6:
            break
    memory["activeProjects"] = projects
    memory["credentials"] = _normalize_string_list(raw.get("credentials"), limit=8, item_limit=120)

    working_style_raw = raw.get("workingStyle") if isinstance(raw.get("workingStyle"), dict) else {}
    memory["workingStyle"] = {
        key: value
        for key, value in {
            "primaryLanguage": _sanitize_text(working_style_raw.get("primaryLanguage"), 24),
            "englishGoal": _sanitize_text(working_style_raw.get("englishGoal"), 120),
            "answerPreference": _normalize_string_list(working_style_raw.get("answerPreference"), limit=8, item_limit=120),
            "decisionStyle": _normalize_string_list(working_style_raw.get("decisionStyle"), limit=8, item_limit=120),
            "tools": _normalize_string_list(working_style_raw.get("tools"), limit=10, item_limit=80),
        }.items()
        if _has_meaningful_value(value)
    }

    memory["currentGaps"] = _normalize_string_list(raw.get("currentGaps"), limit=8, item_limit=180)
    memory["watchItems"] = _normalize_string_list(raw.get("watchItems"), limit=8, item_limit=180)
    memory["openLoops"] = _normalize_string_list(raw.get("openLoops"), limit=8, item_limit=180)
    return memory


def get_minerva_working_memory() -> dict[str, Any]:
    payload = _read_json_file(MINERVA_WORKING_MEMORY_FILE, _default_minerva_working_memory())
    return normalize_minerva_working_memory(payload if isinstance(payload, dict) else None)


def set_minerva_working_memory(payload: dict[str, Any] | None) -> dict[str, Any]:
    memory = normalize_minerva_working_memory(payload)
    _ensure_memory_dir()
    tmp = MINERVA_WORKING_MEMORY_FILE.with_suffix(MINERVA_WORKING_MEMORY_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MINERVA_WORKING_MEMORY_FILE)
    try:
        os.chmod(MINERVA_WORKING_MEMORY_FILE, 0o600)
    except OSError:
        pass
    return memory


def render_minerva_working_memory_context(memory: dict[str, Any] | None = None, *, max_chars: int | None = None) -> str | None:
    data = normalize_minerva_working_memory(memory if isinstance(memory, dict) else get_minerva_working_memory())
    identity = data.get("identity") if isinstance(data.get("identity"), dict) else {}
    trajectory = data.get("careerTrajectory") if isinstance(data.get("careerTrajectory"), dict) else {}
    positioning = data.get("positioning") if isinstance(data.get("positioning"), dict) else {}
    working_style = data.get("workingStyle") if isinstance(data.get("workingStyle"), dict) else {}
    projects = data.get("activeProjects") if isinstance(data.get("activeProjects"), list) else []

    lines = ["Minerva working memory"]
    preferred_name = _sanitize_text(identity.get("preferredName"), 48)
    if preferred_name:
        lines.append(f"- Preferred name: {preferred_name}")
    career_stage = _sanitize_text(identity.get("careerStage"), 80)
    if career_stage:
        lines.append(f"- Current stage: {career_stage}")
    short_term = _sanitize_text(trajectory.get("shortTerm"), 160)
    mid_term = _sanitize_text(trajectory.get("midTerm"), 160)
    long_term = _sanitize_text(trajectory.get("longTerm"), 160)
    if short_term:
        lines.append(f"- Short-term goal: {short_term}")
    if mid_term:
        lines.append(f"- Mid-term goal: {mid_term}")
    if long_term:
        lines.append(f"- Long-term goal: {long_term}")

    thesis = _sanitize_text(positioning.get("thesis"), 140)
    target_role = _sanitize_text(positioning.get("targetRole"), 140)
    if thesis:
        lines.append(f"- Positioning: {thesis}")
    if target_role:
        lines.append(f"- Target role: {target_role}")

    strengths = _normalize_string_list(positioning.get("strengths"), limit=6, item_limit=80)
    if strengths:
        lines.append(f"- Strengths: {', '.join(strengths)}")

    credentials = _normalize_string_list(data.get("credentials"), limit=6, item_limit=100)
    if credentials:
        lines.append(f"- Credentials: {', '.join(credentials)}")

    if projects:
        lines.append("- Active projects:")
        for project in projects[:4]:
            if not isinstance(project, dict):
                continue
            name = _sanitize_text(project.get("name"), 60)
            objective = _sanitize_text(project.get("objective"), 140)
            stage = _sanitize_text(project.get("stage"), 48)
            priority = _sanitize_text(project.get("priority"), 24)
            facts = _normalize_string_list(project.get("facts"), limit=3, item_limit=100)
            project_line = f"  - {name}"
            if stage:
                project_line += f" [{stage}]"
            if priority:
                project_line += f" priority={priority}"
            if objective:
                project_line += f": {objective}"
            lines.append(project_line)
            if facts:
                lines.append(f"    facts: {', '.join(facts)}")

    answer_preference = _normalize_string_list(working_style.get("answerPreference"), limit=6, item_limit=80)
    if answer_preference:
        lines.append(f"- Answer preference: {', '.join(answer_preference)}")

    decision_style = _normalize_string_list(working_style.get("decisionStyle"), limit=6, item_limit=80)
    if decision_style:
        lines.append(f"- Decision style: {', '.join(decision_style)}")

    current_gaps = _normalize_string_list(data.get("currentGaps"), limit=5, item_limit=120)
    if current_gaps:
        lines.append(f"- Current gaps: {', '.join(current_gaps)}")

    open_loops = _normalize_string_list(data.get("openLoops"), limit=5, item_limit=120)
    if open_loops:
        lines.append(f"- Open loops: {', '.join(open_loops)}")

    watch_items = _normalize_string_list(data.get("watchItems"), limit=4, item_limit=120)
    if watch_items:
        lines.append(f"- Watch items: {', '.join(watch_items)}")

    text = "\n".join(lines).strip()
    if not text:
        return None
    max_chars = max(600, int(max_chars or float(os.getenv("MINERVA_WORKING_MEMORY_MAX_CHARS", "1800") or "1800")))
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 1].rstrip()}…"


def _default_clio_knowledge_memory() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tagTaxonomyVersion": "1",
        "projectRegistryVersion": "1",
        "mocRegistryVersion": "1",
        "projects": [],
        "mocs": [],
        "recentNotes": [],
        "dedupeCandidates": [],
    }


def normalize_clio_knowledge_memory(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    memory = _default_clio_knowledge_memory()
    memory["schemaVersion"] = int(raw.get("schemaVersion", 1) or 1)
    memory["updatedAt"] = _sanitize_text(raw.get("updatedAt"), 64) or memory["updatedAt"]
    memory["tagTaxonomyVersion"] = _sanitize_text(raw.get("tagTaxonomyVersion"), 32) or "1"
    memory["projectRegistryVersion"] = _sanitize_text(raw.get("projectRegistryVersion"), 32) or "1"
    memory["mocRegistryVersion"] = _sanitize_text(raw.get("mocRegistryVersion"), 32) or "1"
    memory["projects"] = _normalize_string_list(raw.get("projects"), limit=24, item_limit=80)
    memory["mocs"] = _normalize_string_list(raw.get("mocs"), limit=24, item_limit=120)

    recent_notes: list[dict[str, Any]] = []
    for item in raw.get("recentNotes") or []:
        if not isinstance(item, dict):
            continue
        title = _sanitize_text(item.get("title"), 160)
        note_type = _sanitize_text(item.get("type"), 32)
        vault_file = _sanitize_text(item.get("vaultFile"), 260)
        if not title or not note_type or not vault_file:
            continue
        recent_notes.append(
            {
                "title": title,
                "type": note_type,
                "folder": _sanitize_text(item.get("folder"), 180),
                "templateName": _sanitize_text(item.get("templateName"), 120),
                "vaultFile": vault_file,
                "tags": _normalize_string_list(item.get("tags"), limit=8, item_limit=80),
                "projectLinks": _normalize_string_list(item.get("projectLinks"), limit=6, item_limit=120),
                "mocCandidates": _normalize_string_list(item.get("mocCandidates"), limit=6, item_limit=120),
                "relatedNotes": _normalize_string_list(item.get("relatedNotes"), limit=8, item_limit=120),
                "draftState": _sanitize_text(item.get("draftState"), 32),
                "claimReviewRequired": bool(item.get("claimReviewRequired")),
                "claimReviewId": _sanitize_text(item.get("claimReviewId"), 32),
                "noteAction": _sanitize_text(item.get("noteAction"), 40),
                "updateTarget": _sanitize_text(item.get("updateTarget"), 160),
                "updateTargetPath": _sanitize_text(item.get("updateTargetPath"), 260),
                "mergeCandidates": _normalize_string_list(item.get("mergeCandidates"), limit=6, item_limit=120),
                "mergeCandidatePaths": _normalize_string_list(item.get("mergeCandidatePaths"), limit=6, item_limit=260),
                "suggestionState": _sanitize_text(item.get("suggestionState"), 32),
                "updatedAt": _sanitize_text(item.get("updatedAt"), 64),
            }
        )
        if len(recent_notes) >= 50:
            break
    memory["recentNotes"] = recent_notes

    dedupe_candidates: list[dict[str, Any]] = []
    for item in raw.get("dedupeCandidates") or []:
        if not isinstance(item, dict):
            continue
        title = _sanitize_text(item.get("title"), 160)
        vault_file = _sanitize_text(item.get("vaultFile"), 260)
        if not title or not vault_file:
            continue
        dedupe_candidates.append(
            {
                "title": title,
                "type": _sanitize_text(item.get("type"), 32),
                "vaultFile": vault_file,
                "relatedNotes": _normalize_string_list(item.get("relatedNotes"), limit=6, item_limit=120),
            }
        )
        if len(dedupe_candidates) >= 50:
            break
    memory["dedupeCandidates"] = dedupe_candidates
    return memory


def get_clio_knowledge_memory() -> dict[str, Any]:
    payload = _read_json_file(CLIO_KNOWLEDGE_MEMORY_FILE, _default_clio_knowledge_memory())
    return normalize_clio_knowledge_memory(payload if isinstance(payload, dict) else None)


def render_clio_knowledge_memory_context(memory: dict[str, Any] | None = None, *, max_chars: int = 1600) -> str | None:
    data = normalize_clio_knowledge_memory(memory if isinstance(memory, dict) else get_clio_knowledge_memory())
    lines = ["Clio knowledge memory"]
    pending_claim_reviews = len(list_pending_clio_claim_reviews(limit=200))
    if pending_claim_reviews:
        lines.append(f"- Pending claim reviews: {pending_claim_reviews}")
    projects = _normalize_string_list(data.get("projects"), limit=8, item_limit=80)
    if projects:
        lines.append(f"- Registered projects: {', '.join(projects)}")
    mocs = _normalize_string_list(data.get("mocs"), limit=8, item_limit=120)
    if mocs:
        lines.append(f"- MOC registry: {', '.join(mocs)}")
    recent_notes = data.get("recentNotes") if isinstance(data.get("recentNotes"), list) else []
    if recent_notes:
        lines.append("- Recent notes:")
        for item in recent_notes[:5]:
            if not isinstance(item, dict):
                continue
            title = _sanitize_text(item.get("title"), 90)
            note_type = _sanitize_text(item.get("type"), 24)
            draft_state = _sanitize_text(item.get("draftState"), 24)
            note_action = _sanitize_text(item.get("noteAction"), 32)
            suggestion_state = _sanitize_text(item.get("suggestionState"), 24)
            suffix = f" action={note_action}" if note_action else ""
            if suggestion_state:
                suffix += f" suggestion={suggestion_state}"
            lines.append(f"  - {title} [{note_type}] state={draft_state or 'draft'}{suffix}")
    dedupe_candidates = data.get("dedupeCandidates") if isinstance(data.get("dedupeCandidates"), list) else []
    if dedupe_candidates:
        lines.append("- Dedupe candidates:")
        for item in dedupe_candidates[:4]:
            if not isinstance(item, dict):
                continue
            title = _sanitize_text(item.get("title"), 90)
            related = _normalize_string_list(item.get("relatedNotes"), limit=2, item_limit=80)
            lines.append(f"  - {title}" + (f" -> {', '.join(related)}" if related else ""))
    text = "\n".join(lines).strip()
    return text[: max_chars - 1].rstrip() + "…" if len(text) > max_chars else text or None


def _default_hermes_evidence_memory() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "topics": [],
    }


def normalize_hermes_evidence_memory(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    memory = _default_hermes_evidence_memory()
    memory["schemaVersion"] = int(raw.get("schemaVersion", 1) or 1)
    memory["updatedAt"] = _sanitize_text(raw.get("updatedAt"), 64) or memory["updatedAt"]
    topics: list[dict[str, Any]] = []
    for item in raw.get("topics") or []:
        if not isinstance(item, dict):
            continue
        topic_key = _sanitize_text(item.get("topicKey"), 120)
        if not topic_key:
            continue
        topics.append(
            {
                "topicKey": topic_key,
                "title": _sanitize_text(item.get("title"), 160),
                "dedupeKey": _sanitize_text(item.get("dedupeKey"), 40),
                "trustScore": round(float(item.get("trustScore", 0) or 0), 4),
                "lastSeenAt": _sanitize_text(item.get("lastSeenAt"), 64),
                "lastPriority": _sanitize_text(item.get("lastPriority"), 24),
                "lastDecision": _sanitize_text(item.get("lastDecision"), 40),
                "sourceTitles": _normalize_string_list(item.get("sourceTitles"), limit=6, item_limit=100),
                "sourceDomains": _normalize_string_list(item.get("sourceDomains"), limit=6, item_limit=60),
            }
        )
        if len(topics) >= 80:
            break
    memory["topics"] = topics
    return memory


def get_hermes_evidence_memory() -> dict[str, Any]:
    payload = _read_json_file(HERMES_EVIDENCE_MEMORY_FILE, _default_hermes_evidence_memory())
    return normalize_hermes_evidence_memory(payload if isinstance(payload, dict) else None)


def upsert_hermes_evidence_memory(event: dict[str, Any]) -> None:
    if str(event.get("agentId")) != "hermes":
        return
    memory = get_hermes_evidence_memory()
    topics = memory.get("topics") if isinstance(memory.get("topics"), list) else []
    source_titles: list[str] = []
    source_domains: list[str] = []
    for ref in event.get("sourceRefs") or []:
        if not isinstance(ref, dict):
            continue
        title = _sanitize_text(ref.get("title"), 100)
        if title:
            source_titles.append(title)
        domain = _sanitize_text(ref.get("domain"), 60) or _sanitize_text(ref.get("category"), 60)
        if domain:
            source_domains.append(domain)
    trust_score = 0.0
    try:
        trust_score = max(float(event.get("confidence", 0) or 0), float(event.get("impactScore", 0) or 0))
    except (TypeError, ValueError):
        trust_score = 0.0
    entry = {
        "topicKey": _sanitize_text(event.get("topicKey"), 120),
        "title": _sanitize_text(event.get("title"), 160),
        "dedupeKey": _sanitize_text(event.get("dedupeKey"), 40),
        "trustScore": round(trust_score, 4),
        "lastSeenAt": _sanitize_text(event.get("createdAt"), 64),
        "lastPriority": _sanitize_text(event.get("priority"), 24),
        "lastDecision": _sanitize_text(((event.get("payload") or {}).get("orchestration") or {}).get("decision"), 40),
        "sourceTitles": _normalize_string_list(source_titles, limit=6, item_limit=100),
        "sourceDomains": _normalize_string_list(source_domains, limit=6, item_limit=60),
    }
    filtered = [item for item in topics if isinstance(item, dict) and str(item.get("topicKey")) != entry["topicKey"]]
    filtered.insert(0, entry)
    memory["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    memory["topics"] = filtered[:80]
    _write_json_file(HERMES_EVIDENCE_MEMORY_FILE, memory)
    try:
        os.chmod(HERMES_EVIDENCE_MEMORY_FILE, 0o600)
    except OSError:
        pass


def render_hermes_evidence_memory_context(
    memory: dict[str, Any] | None = None,
    *,
    topic_key: str | None = None,
    max_chars: int = 1400,
) -> str | None:
    data = normalize_hermes_evidence_memory(memory if isinstance(memory, dict) else get_hermes_evidence_memory())
    topics = data.get("topics") if isinstance(data.get("topics"), list) else []
    if topic_key:
        topics = [item for item in topics if isinstance(item, dict) and str(item.get("topicKey")) == topic_key]
    lines = ["Hermes evidence memory"]
    for item in topics[:6]:
        if not isinstance(item, dict):
            continue
        title = _sanitize_text(item.get("title"), 90) or _sanitize_text(item.get("topicKey"), 90)
        trust = item.get("trustScore")
        priority = _sanitize_text(item.get("lastPriority"), 24)
        decision = _sanitize_text(item.get("lastDecision"), 32)
        source_domains = _normalize_string_list(item.get("sourceDomains"), limit=3, item_limit=60)
        line = f"- {title}"
        if priority:
            line += f" priority={priority}"
        if isinstance(trust, (int, float)):
            line += f" trust={trust:.2f}"
        if decision:
            line += f" decision={decision}"
        if source_domains:
            line += f" sources={', '.join(source_domains)}"
        lines.append(line)
    text = "\n".join(lines).strip()
    return text[: max_chars - 1].rstrip() + "…" if len(text) > max_chars else text or None
