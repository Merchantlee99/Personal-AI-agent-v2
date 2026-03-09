from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path((os.getenv("SHARED_ROOT_PATH") or "/app/shared_data").strip() or "/app/shared_data")
MEMORY_DIR = ROOT / "shared_memory"
LOGS_DIR = ROOT / "logs"
EVENTS_FILE = MEMORY_DIR / "agent_events.json"
COOLDOWN_FILE = MEMORY_DIR / "topic_cooldowns.json"
DIGEST_FILE = MEMORY_DIR / "digest_queue.json"
TELEGRAM_CHAT_HISTORY_FILE = MEMORY_DIR / "telegram_chat_history.json"
MEMORY_MARKDOWN_FILE = MEMORY_DIR / "memory.md"
MINERVA_WORKING_MEMORY_FILE = MEMORY_DIR / "minerva_working_memory.json"
CLIO_KNOWLEDGE_MEMORY_FILE = MEMORY_DIR / "clio_knowledge_memory.json"
HERMES_EVIDENCE_MEMORY_FILE = MEMORY_DIR / "hermes_evidence_memory.json"
MORNING_BRIEFING_OBSERVATIONS_FILE = LOGS_DIR / "morning_briefing_observations.jsonl"

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


def _ensure_memory_dir() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_logs_dir() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def read_json_file(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return fallback


def write_json_file(path: Path, payload: Any) -> None:
    _ensure_memory_dir()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def append_jsonl_file(path: Path, payload: dict[str, Any]) -> None:
    if path.parent == MEMORY_DIR:
        _ensure_memory_dir()
    else:
        _ensure_logs_dir()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")


def single_line(value: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", str(value or "")).replace("|", "\\|").strip()
    if not compact:
        return "-"
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(8, limit - 1)].rstrip()}…"


def sanitize_text(value: Any, limit: int = 240) -> str:
    compact = re.sub(r"\s+", " ", str(value or "")).strip()
    if not compact:
        return ""
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(16, limit - 1)].rstrip()}…"


def parse_iso_datetime(value: Any) -> datetime | None:
    compact = sanitize_text(value, 64)
    if not compact:
        return None
    try:
        return datetime.fromisoformat(compact.replace("Z", "+00:00"))
    except ValueError:
        return None


def safe_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def normalize_string_list(values: Any, *, limit: int = 12, item_limit: int = 180) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        token = sanitize_text(item, item_limit)
        key = token.lower()
        if not token or key in seen:
            continue
        normalized.append(token)
        seen.add(key)
        if len(normalized) >= max(1, limit):
            break
    return normalized


def normalize_project_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    name = sanitize_text(entry.get("name"), 80)
    if not name:
        return None
    project = {
        "name": name,
        "role": sanitize_text(entry.get("role"), 80),
        "stage": sanitize_text(entry.get("stage"), 80),
        "priority": sanitize_text(entry.get("priority"), 32),
        "objective": sanitize_text(entry.get("objective"), 220),
        "facts": normalize_string_list(entry.get("facts"), limit=6, item_limit=180),
    }
    return {key: value for key, value in project.items() if has_meaningful_value(value)}


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


def append_memory_block(lines: list[str]) -> None:
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
        f"### {event.get('createdAt')} [{event.get('agentId')}] {single_line(str(event.get('title', '')), 90)}",
        f"- event_id: {event.get('eventId')}",
        f"- topic: {single_line(str(event.get('topicKey', '')), 84)}",
        f"- priority_confidence: {event.get('priority')}/{event.get('confidence')}",
        f"- tags: {single_line(', '.join(event.get('tags') or []), 160)}",
        f"- summary: {single_line(str(event.get('summary', '')), 220)}",
    ]
    for source in (event.get("sourceRefs") or [])[:4]:
        title = single_line(str(source.get("title", "")), 84)
        url = single_line(str(source.get("url", "")), 140)
        lines.append(f"- source: {title} | {url}")
    append_memory_block(lines)


def append_agent_event(event: dict[str, Any]) -> None:
    events = read_json_file(EVENTS_FILE, [])
    if not isinstance(events, list):
        events = []
    events.append(event)
    write_json_file(EVENTS_FILE, events[-3000:])
    try:
        from .orch_role_memories import upsert_hermes_evidence_memory

        upsert_hermes_evidence_memory(event)
    except Exception:  # noqa: BLE001
        pass


def append_morning_briefing_observation(observation: dict[str, Any]) -> None:
    payload = {
        "observedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        **observation,
    }
    append_jsonl_file(MORNING_BRIEFING_OBSERVATIONS_FILE, payload)


def list_agent_events() -> list[dict[str, Any]]:
    payload = read_json_file(EVENTS_FILE, [])
    return payload if isinstance(payload, list) else []


def find_event_by_id(event_id: str) -> dict[str, Any] | None:
    for event in list_agent_events():
        if str(event.get("eventId")) == event_id:
            return event
    return None


def get_cooldown(topic_key: str) -> str | None:
    cooldowns = read_json_file(COOLDOWN_FILE, {})
    if not isinstance(cooldowns, dict):
        return None
    value = cooldowns.get(topic_key)
    return str(value) if isinstance(value, str) and value.strip() else None


def set_cooldown(topic_key: str, until_iso: str) -> None:
    cooldowns = read_json_file(COOLDOWN_FILE, {})
    if not isinstance(cooldowns, dict):
        cooldowns = {}
    cooldowns[topic_key] = until_iso
    write_json_file(COOLDOWN_FILE, cooldowns)


def push_digest_item(slot: str, event: dict[str, Any]) -> None:
    queue = read_json_file(DIGEST_FILE, {})
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
    write_json_file(DIGEST_FILE, queue)


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


def get_telegram_chat_history(chat_id: str, limit: int = 12) -> list[dict[str, str]]:
    payload = read_json_file(TELEGRAM_CHAT_HISTORY_FILE, {})
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
    append_memory_block(
        [
            f"### {at} [telegram][chat:{single_line(chat_id, 48)}]",
            f"- user: {single_line(user_text, 180)}",
            f"- minerva: {single_line(assistant_text, 220)}",
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
    payload = read_json_file(TELEGRAM_CHAT_HISTORY_FILE, {})
    if not isinstance(payload, dict):
        payload = {}
    current = payload.get(chat_id) if isinstance(payload.get(chat_id), list) else []
    current.append({"role": "user", "text": user_text, "at": now})
    current.append({"role": "assistant", "text": assistant_text, "at": now})
    payload[chat_id] = current[-max_entries:]
    write_json_file(TELEGRAM_CHAT_HISTORY_FILE, payload)
    try:
        _append_telegram_turn_to_memory(chat_id, user_text, assistant_text, now)
    except Exception:  # noqa: BLE001
        pass


def clear_telegram_chat_history(chat_id: str) -> None:
    payload = read_json_file(TELEGRAM_CHAT_HISTORY_FILE, {})
    if not isinstance(payload, dict):
        return
    if chat_id in payload:
        del payload[chat_id]
        write_json_file(TELEGRAM_CHAT_HISTORY_FILE, payload)


def get_runtime_memory_markdown_path() -> str:
    _ensure_memory_md()
    return str(MEMORY_MARKDOWN_FILE)
