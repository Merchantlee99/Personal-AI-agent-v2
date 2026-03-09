from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


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


def vault_root(root: Path) -> Path:
    return (root / "obsidian_vault").resolve()


def safe_vault_path(root: Path, relative_or_absolute: str, sanitize_text: Callable[[Any, int], str]) -> Path | None:
    raw = sanitize_text(relative_or_absolute, 260)
    if not raw:
        return None
    candidate = Path(raw)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(vault_root(root))
    except ValueError:
        return None
    return resolved


def update_frontmatter_scalar(markdown: str, key: str, value: str) -> str:
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


def make_clio_note_suggestion_id(vault_file: str) -> str:
    return hashlib.sha256(str(vault_file).encode("utf-8")).hexdigest()[:12]


def make_clio_note_suggestion_fingerprint(
    item: dict[str, Any],
    *,
    sanitize_text: Callable[[Any, int], str],
    normalize_string_list: Callable[..., list[str]],
) -> str:
    payload = {
        "title": sanitize_text(item.get("title"), 160),
        "noteAction": sanitize_text(item.get("noteAction"), 40),
        "updateTargetPath": sanitize_text(item.get("updateTargetPath"), 260),
        "mergeCandidatePaths": normalize_string_list(item.get("mergeCandidatePaths"), limit=6, item_limit=260),
        "relatedNotes": normalize_string_list(item.get("relatedNotes"), limit=8, item_limit=120),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def normalize_clio_note_suggestion(
    item: dict[str, Any],
    *,
    sanitize_text: Callable[[Any, int], str],
    normalize_string_list: Callable[..., list[str]],
) -> dict[str, Any] | None:
    title = sanitize_text(item.get("title"), 180)
    note_action = sanitize_text(item.get("noteAction"), 40)
    vault_file = sanitize_text(item.get("vaultFile"), 260)
    if not title or not vault_file or note_action not in {"update_candidate", "merge_candidate"}:
        return None
    fingerprint = sanitize_text(item.get("suggestionFingerprint"), 64) or make_clio_note_suggestion_fingerprint(
        item,
        sanitize_text=sanitize_text,
        normalize_string_list=normalize_string_list,
    )
    return {
        "id": make_clio_note_suggestion_id(vault_file),
        "title": title,
        "type": sanitize_text(item.get("type"), 32),
        "vaultFile": vault_file,
        "draftState": sanitize_text(item.get("draftState"), 32) or "draft",
        "noteAction": note_action,
        "updateTarget": sanitize_text(item.get("updateTarget"), 160),
        "updateTargetPath": sanitize_text(item.get("updateTargetPath"), 260),
        "mergeCandidates": normalize_string_list(item.get("mergeCandidates"), limit=6, item_limit=120),
        "mergeCandidatePaths": normalize_string_list(item.get("mergeCandidatePaths"), limit=6, item_limit=260),
        "projectLinks": normalize_string_list(item.get("projectLinks"), limit=6, item_limit=120),
        "mocCandidates": normalize_string_list(item.get("mocCandidates"), limit=6, item_limit=120),
        "suggestionScore": item.get("suggestionScore") if item.get("suggestionScore") not in {None, ""} else None,
        "suggestionReasons": normalize_string_list(item.get("suggestionReasons"), limit=4, item_limit=140),
        "suggestionState": sanitize_text(item.get("suggestionState"), 32) or "pending",
        "suggestionFingerprint": fingerprint,
        "dismissedAt": sanitize_text(item.get("dismissedAt"), 64),
        "dismissedSuggestionFingerprint": sanitize_text(item.get("dismissedSuggestionFingerprint"), 64),
        "suggestionCooldownUntil": sanitize_text(item.get("suggestionCooldownUntil"), 64),
        "updatedAt": sanitize_text(item.get("updatedAt"), 64),
    }


def update_clio_note_suggestion_state(
    *,
    clio_memory_file: Path,
    get_clio_knowledge_memory: Callable[[], dict[str, Any]],
    write_json_file: Callable[[Path, Any], None],
    suggestion_id: str,
    suggestion_state: str,
    draft_state: str | None = None,
    suggestion_fingerprint: str | None = None,
    dismissed_at: str | None = None,
    dismissed_fingerprint: str | None = None,
    cooldown_until: str | None = None,
    updated_at: str | None = None,
    sanitize_text: Callable[[Any, int], str] | None = None,
) -> dict[str, Any] | None:
    memory = get_clio_knowledge_memory()
    changed = False
    updated_note: dict[str, Any] | None = None
    sanitize = sanitize_text or (lambda value, limit=240: str(value or "")[:limit])
    for item in memory.get("recentNotes", []):
        if not isinstance(item, dict):
            continue
        vault_file = sanitize(item.get("vaultFile"), 260)
        if make_clio_note_suggestion_id(vault_file) != suggestion_id:
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
        item["updatedAt"] = sanitize(updated_at, 64) or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        updated_note = dict(item)
        changed = True
        break
    if not changed:
        return None
    memory["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    write_json_file(clio_memory_file, memory)
    return updated_note


def strip_frontmatter(markdown: str) -> str:
    if not markdown.startswith("---"):
        return markdown
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return markdown
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[index + 1 :]).strip()
    return markdown


def extract_diff_candidate_lines(markdown: str, *, sanitize_text: Callable[[Any, int], str], limit: int = 3) -> list[str]:
    body = strip_frontmatter(markdown)
    lines: list[str] = []
    for raw in body.splitlines():
        line = sanitize_text(raw, 180)
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


def build_clio_note_diff_summary(
    suggestion: dict[str, Any],
    *,
    root: Path,
    sanitize_text: Callable[[Any, int], str],
) -> list[str]:
    draft_path = safe_vault_path(root, str(suggestion.get("vaultFile") or ""), sanitize_text)
    if not draft_path or not draft_path.is_file():
        return []

    draft_lines = extract_diff_candidate_lines(draft_path.read_text(encoding="utf-8"), sanitize_text=sanitize_text, limit=4)
    if not draft_lines:
        return []

    if suggestion.get("noteAction") == "update_candidate":
        target_path = safe_vault_path(root, str(suggestion.get("updateTargetPath") or ""), sanitize_text)
        target_body = target_path.read_text(encoding="utf-8") if target_path and target_path.is_file() else ""
        target_text = sanitize_text(strip_frontmatter(target_body), 5000)
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
        line = sanitize_text(item, 120)
        if line:
            summary.append(f"연결 후보: {line}")
    return summary or draft_lines[:2]


def reactivate_clio_note_suggestion_if_due(
    *,
    suggestion_id: str,
    item: dict[str, Any],
    parse_iso_datetime: Callable[[Any], datetime | None],
    sanitize_text: Callable[[Any, int], str],
    make_fingerprint: Callable[[dict[str, Any]], str],
    update_state: Callable[..., dict[str, Any] | None],
) -> bool:
    state = sanitize_text(item.get("suggestionState"), 32) or "pending"
    if state != "dismissed":
        return False

    now = datetime.now(timezone.utc)
    cooldown_until = parse_iso_datetime(item.get("suggestionCooldownUntil"))
    dismissed_at = parse_iso_datetime(item.get("dismissedAt"))
    current_fingerprint = sanitize_text(item.get("suggestionFingerprint"), 64) or make_fingerprint(item)
    dismissed_fingerprint = sanitize_text(item.get("dismissedSuggestionFingerprint"), 64)
    updated_at = parse_iso_datetime(item.get("updatedAt"))

    if dismissed_fingerprint and current_fingerprint != dismissed_fingerprint:
        update_state(
            suggestion_id=suggestion_id,
            suggestion_state="pending",
            suggestion_fingerprint=current_fingerprint,
            dismissed_at="",
            dismissed_fingerprint="",
            cooldown_until="",
        )
        return True
    if dismissed_at and updated_at and updated_at > dismissed_at:
        update_state(
            suggestion_id=suggestion_id,
            suggestion_state="pending",
            suggestion_fingerprint=current_fingerprint,
            dismissed_at="",
            dismissed_fingerprint="",
            cooldown_until="",
        )
        return True
    if cooldown_until and now >= cooldown_until:
        update_state(
            suggestion_id=suggestion_id,
            suggestion_state="pending",
            suggestion_fingerprint=current_fingerprint,
            dismissed_at="",
            dismissed_fingerprint="",
            cooldown_until="",
        )
        return True
    return False


def append_note_annotation(note_path: Path, marker: str, heading: str, lines: list[str]) -> bool:
    if not note_path.is_file():
        return False
    markdown = note_path.read_text(encoding="utf-8")
    if marker in markdown:
        return True
    block = "\n".join(["", heading, marker, *lines]).rstrip() + "\n"
    note_path.write_text(markdown.rstrip() + "\n" + block, encoding="utf-8")
    return True


def render_clio_knowledge_memory_context(
    *,
    memory: dict[str, Any],
    pending_claim_reviews: int,
    pending_suggestions: int,
    sanitize_text: Callable[[Any, int], str],
    normalize_clio_knowledge_memory: Callable[[dict[str, Any] | None], dict[str, Any]],
    normalize_string_list: Callable[..., list[str]],
    max_chars: int,
) -> str | None:
    data = normalize_clio_knowledge_memory(memory if isinstance(memory, dict) else None)
    lines = ["Clio knowledge memory"]
    if pending_claim_reviews:
        lines.append(f"- Pending claim reviews: {pending_claim_reviews}")
    if pending_suggestions:
        lines.append(f"- Pending note suggestions: {pending_suggestions}")
    projects = normalize_string_list(data.get("projects"), limit=8, item_limit=80)
    if projects:
        lines.append(f"- Registered projects: {', '.join(projects)}")
    mocs = normalize_string_list(data.get("mocs"), limit=8, item_limit=120)
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
                and (bool(item.get("claimReviewRequired")) or sanitize_text(item.get("suggestionState"), 24) == "pending")
            ]
        )
        prioritized.extend([item for item in recent_notes if isinstance(item, dict) and item not in prioritized])
        for item in prioritized[:4]:
            if not isinstance(item, dict):
                continue
            title = sanitize_text(item.get("title"), 90)
            note_type = sanitize_text(item.get("type"), 24)
            draft_state = sanitize_text(item.get("draftState"), 24)
            note_action = sanitize_text(item.get("noteAction"), 32)
            suggestion_state = sanitize_text(item.get("suggestionState"), 24)
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
            title = sanitize_text(item.get("title"), 90)
            related = normalize_string_list(item.get("relatedNotes"), limit=2, item_limit=80)
            lines.append(f"  - {title}" + (f" -> {', '.join(related)}" if related else ""))
    text = "\n".join(lines).strip()
    return text[: max_chars - 1].rstrip() + "…" if len(text) > max_chars else text or None
