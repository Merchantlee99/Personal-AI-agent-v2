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


def default_minerva_working_memory() -> dict[str, Any]:
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


def normalize_minerva_working_memory(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    memory = default_minerva_working_memory()
    memory["schemaVersion"] = int(raw.get("schemaVersion", 1) or 1)
    memory["updatedAt"] = sanitize_text(
        raw.get("updatedAt") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        64,
    )

    identity_raw = raw.get("identity") if isinstance(raw.get("identity"), dict) else {}
    memory["identity"] = {
        key: value
        for key, value in {
            "preferredName": sanitize_text(identity_raw.get("preferredName"), 48),
            "legalName": sanitize_text(identity_raw.get("legalName"), 48),
            "locale": sanitize_text(identity_raw.get("locale"), 24),
            "timezone": sanitize_text(identity_raw.get("timezone"), 48),
            "careerStage": sanitize_text(identity_raw.get("careerStage"), 80),
        }.items()
        if value
    }

    trajectory_raw = raw.get("careerTrajectory") if isinstance(raw.get("careerTrajectory"), dict) else {}
    memory["careerTrajectory"] = {
        key: value
        for key, value in {
            "shortTerm": sanitize_text(trajectory_raw.get("shortTerm"), 160),
            "midTerm": sanitize_text(trajectory_raw.get("midTerm"), 160),
            "longTerm": sanitize_text(trajectory_raw.get("longTerm"), 160),
            "educationPlan": normalize_string_list(trajectory_raw.get("educationPlan"), limit=4, item_limit=120),
        }.items()
        if has_meaningful_value(value)
    }

    positioning_raw = raw.get("positioning") if isinstance(raw.get("positioning"), dict) else {}
    memory["positioning"] = {
        key: value
        for key, value in {
            "thesis": sanitize_text(positioning_raw.get("thesis"), 120),
            "targetRole": sanitize_text(positioning_raw.get("targetRole"), 120),
            "strengths": normalize_string_list(positioning_raw.get("strengths"), limit=8, item_limit=120),
            "targetCompanies": normalize_string_list(positioning_raw.get("targetCompanies"), limit=8, item_limit=80),
        }.items()
        if has_meaningful_value(value)
    }

    projects = []
    for item in raw.get("activeProjects") or []:
        project = normalize_project_entry(item)
        if project:
            projects.append(project)
        if len(projects) >= 6:
            break
    memory["activeProjects"] = projects
    memory["credentials"] = normalize_string_list(raw.get("credentials"), limit=8, item_limit=120)

    working_style_raw = raw.get("workingStyle") if isinstance(raw.get("workingStyle"), dict) else {}
    memory["workingStyle"] = {
        key: value
        for key, value in {
            "primaryLanguage": sanitize_text(working_style_raw.get("primaryLanguage"), 24),
            "englishGoal": sanitize_text(working_style_raw.get("englishGoal"), 120),
            "answerPreference": normalize_string_list(working_style_raw.get("answerPreference"), limit=8, item_limit=120),
            "decisionStyle": normalize_string_list(working_style_raw.get("decisionStyle"), limit=8, item_limit=120),
            "tools": normalize_string_list(working_style_raw.get("tools"), limit=10, item_limit=80),
        }.items()
        if has_meaningful_value(value)
    }

    memory["currentGaps"] = normalize_string_list(raw.get("currentGaps"), limit=8, item_limit=180)
    memory["watchItems"] = normalize_string_list(raw.get("watchItems"), limit=8, item_limit=180)
    memory["openLoops"] = normalize_string_list(raw.get("openLoops"), limit=8, item_limit=180)
    return memory


def get_minerva_working_memory() -> dict[str, Any]:
    payload = read_json_file(MINERVA_WORKING_MEMORY_FILE, default_minerva_working_memory())
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
    preferred_name = sanitize_text(identity.get("preferredName"), 48)
    if preferred_name:
        lines.append(f"- Preferred name: {preferred_name}")
    career_stage = sanitize_text(identity.get("careerStage"), 80)
    if career_stage:
        lines.append(f"- Current stage: {career_stage}")
    short_term = sanitize_text(trajectory.get("shortTerm"), 160)
    mid_term = sanitize_text(trajectory.get("midTerm"), 160)
    long_term = sanitize_text(trajectory.get("longTerm"), 160)
    if short_term:
        lines.append(f"- Short-term goal: {short_term}")
    if mid_term:
        lines.append(f"- Mid-term goal: {mid_term}")
    if long_term:
        lines.append(f"- Long-term goal: {long_term}")

    thesis = sanitize_text(positioning.get("thesis"), 140)
    target_role = sanitize_text(positioning.get("targetRole"), 140)
    if thesis:
        lines.append(f"- Positioning: {thesis}")
    if target_role:
        lines.append(f"- Target role: {target_role}")

    strengths = normalize_string_list(positioning.get("strengths"), limit=6, item_limit=80)
    if strengths:
        lines.append(f"- Strengths: {', '.join(strengths)}")

    credentials = normalize_string_list(data.get("credentials"), limit=6, item_limit=100)
    if credentials:
        lines.append(f"- Credentials: {', '.join(credentials)}")

    if projects:
        lines.append("- Active projects:")
        for project in projects[:4]:
            if not isinstance(project, dict):
                continue
            name = sanitize_text(project.get("name"), 60)
            objective = sanitize_text(project.get("objective"), 140)
            stage = sanitize_text(project.get("stage"), 48)
            priority = sanitize_text(project.get("priority"), 24)
            facts = normalize_string_list(project.get("facts"), limit=3, item_limit=100)
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

    answer_preference = normalize_string_list(working_style.get("answerPreference"), limit=6, item_limit=80)
    if answer_preference:
        lines.append(f"- Answer preference: {', '.join(answer_preference)}")

    decision_style = normalize_string_list(working_style.get("decisionStyle"), limit=6, item_limit=80)
    if decision_style:
        lines.append(f"- Decision style: {', '.join(decision_style)}")

    current_gaps = normalize_string_list(data.get("currentGaps"), limit=5, item_limit=120)
    if current_gaps:
        lines.append(f"- Current gaps: {', '.join(current_gaps)}")

    open_loops = normalize_string_list(data.get("openLoops"), limit=5, item_limit=120)
    if open_loops:
        lines.append(f"- Open loops: {', '.join(open_loops)}")

    watch_items = normalize_string_list(data.get("watchItems"), limit=4, item_limit=120)
    if watch_items:
        lines.append(f"- Watch items: {', '.join(watch_items)}")

    text = "\n".join(lines).strip()
    if not text:
        return None
    max_chars = max(600, int(max_chars or float(os.getenv("MINERVA_WORKING_MEMORY_MAX_CHARS", "1800") or "1800")))
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 1].rstrip()}…"


def default_clio_knowledge_memory() -> dict[str, Any]:
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
    memory = default_clio_knowledge_memory()
    memory["schemaVersion"] = int(raw.get("schemaVersion", 1) or 1)
    memory["updatedAt"] = sanitize_text(raw.get("updatedAt"), 64) or memory["updatedAt"]
    memory["tagTaxonomyVersion"] = sanitize_text(raw.get("tagTaxonomyVersion"), 32) or "1"
    memory["projectRegistryVersion"] = sanitize_text(raw.get("projectRegistryVersion"), 32) or "1"
    memory["mocRegistryVersion"] = sanitize_text(raw.get("mocRegistryVersion"), 32) or "1"
    memory["projects"] = normalize_string_list(raw.get("projects"), limit=24, item_limit=80)
    memory["mocs"] = normalize_string_list(raw.get("mocs"), limit=24, item_limit=120)

    recent_notes: list[dict[str, Any]] = []
    for item in raw.get("recentNotes") or []:
        if not isinstance(item, dict):
            continue
        title = sanitize_text(item.get("title"), 160)
        note_type = sanitize_text(item.get("type"), 32)
        vault_file = sanitize_text(item.get("vaultFile"), 260)
        if not title or not note_type or not vault_file:
            continue
        recent_notes.append(
            {
                "title": title,
                "type": note_type,
                "folder": sanitize_text(item.get("folder"), 180),
                "templateName": sanitize_text(item.get("templateName"), 120),
                "vaultFile": vault_file,
                "tags": normalize_string_list(item.get("tags"), limit=8, item_limit=80),
                "projectLinks": normalize_string_list(item.get("projectLinks"), limit=6, item_limit=120),
                "mocCandidates": normalize_string_list(item.get("mocCandidates"), limit=6, item_limit=120),
                "relatedNotes": normalize_string_list(item.get("relatedNotes"), limit=8, item_limit=120),
                "draftState": sanitize_text(item.get("draftState"), 32),
                "claimReviewRequired": bool(item.get("claimReviewRequired")),
                "claimReviewId": sanitize_text(item.get("claimReviewId"), 32),
                "noteAction": sanitize_text(item.get("noteAction"), 40),
                "updateTarget": sanitize_text(item.get("updateTarget"), 160),
                "updateTargetPath": sanitize_text(item.get("updateTargetPath"), 260),
                "mergeCandidates": normalize_string_list(item.get("mergeCandidates"), limit=6, item_limit=120),
                "mergeCandidatePaths": normalize_string_list(item.get("mergeCandidatePaths"), limit=6, item_limit=260),
                "suggestionScore": safe_float(item.get("suggestionScore")),
                "suggestionReasons": normalize_string_list(item.get("suggestionReasons"), limit=4, item_limit=140),
                "suggestionState": sanitize_text(item.get("suggestionState"), 32),
                "suggestionFingerprint": sanitize_text(item.get("suggestionFingerprint"), 64),
                "dismissedAt": sanitize_text(item.get("dismissedAt"), 64),
                "dismissedSuggestionFingerprint": sanitize_text(item.get("dismissedSuggestionFingerprint"), 64),
                "suggestionCooldownUntil": sanitize_text(item.get("suggestionCooldownUntil"), 64),
                "updatedAt": sanitize_text(item.get("updatedAt"), 64),
            }
        )
        if len(recent_notes) >= 50:
            break
    memory["recentNotes"] = recent_notes

    dedupe_candidates: list[dict[str, Any]] = []
    for item in raw.get("dedupeCandidates") or []:
        if not isinstance(item, dict):
            continue
        title = sanitize_text(item.get("title"), 160)
        vault_file = sanitize_text(item.get("vaultFile"), 260)
        if not title or not vault_file:
            continue
        dedupe_candidates.append(
            {
                "title": title,
                "type": sanitize_text(item.get("type"), 32),
                "vaultFile": vault_file,
                "relatedNotes": normalize_string_list(item.get("relatedNotes"), limit=6, item_limit=120),
            }
        )
        if len(dedupe_candidates) >= 50:
            break
    memory["dedupeCandidates"] = dedupe_candidates
    return memory


def get_clio_knowledge_memory() -> dict[str, Any]:
    payload = read_json_file(CLIO_KNOWLEDGE_MEMORY_FILE, default_clio_knowledge_memory())
    return normalize_clio_knowledge_memory(payload if isinstance(payload, dict) else None)


def default_hermes_evidence_memory() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "topics": [],
    }


def normalize_hermes_evidence_memory(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    memory = default_hermes_evidence_memory()
    memory["schemaVersion"] = int(raw.get("schemaVersion", 1) or 1)
    memory["updatedAt"] = sanitize_text(raw.get("updatedAt"), 64) or memory["updatedAt"]
    topics: list[dict[str, Any]] = []
    for item in raw.get("topics") or []:
        if not isinstance(item, dict):
            continue
        topic_key = sanitize_text(item.get("topicKey"), 120)
        if not topic_key:
            continue
        topics.append(
            {
                "topicKey": topic_key,
                "title": sanitize_text(item.get("title"), 160),
                "dedupeKey": sanitize_text(item.get("dedupeKey"), 40),
                "trustScore": round(float(item.get("trustScore", 0) or 0), 4),
                "lastSeenAt": sanitize_text(item.get("lastSeenAt"), 64),
                "lastPriority": sanitize_text(item.get("lastPriority"), 24),
                "lastDecision": sanitize_text(item.get("lastDecision"), 40),
                "sourceTitles": normalize_string_list(item.get("sourceTitles"), limit=6, item_limit=100),
                "sourceDomains": normalize_string_list(item.get("sourceDomains"), limit=6, item_limit=60),
            }
        )
        if len(topics) >= 80:
            break
    memory["topics"] = topics
    return memory


def get_hermes_evidence_memory() -> dict[str, Any]:
    payload = read_json_file(HERMES_EVIDENCE_MEMORY_FILE, default_hermes_evidence_memory())
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
        title = sanitize_text(ref.get("title"), 100)
        if title:
            source_titles.append(title)
        domain = sanitize_text(ref.get("domain"), 60) or sanitize_text(ref.get("category"), 60)
        if domain:
            source_domains.append(domain)
    trust_score = 0.0
    try:
        trust_score = max(float(event.get("confidence", 0) or 0), float(event.get("impactScore", 0) or 0))
    except (TypeError, ValueError):
        trust_score = 0.0
    entry = {
        "topicKey": sanitize_text(event.get("topicKey"), 120),
        "title": sanitize_text(event.get("title"), 160),
        "dedupeKey": sanitize_text(event.get("dedupeKey"), 40),
        "trustScore": round(trust_score, 4),
        "lastSeenAt": sanitize_text(event.get("createdAt"), 64),
        "lastPriority": sanitize_text(event.get("priority"), 24),
        "lastDecision": sanitize_text(((event.get("payload") or {}).get("orchestration") or {}).get("decision"), 40),
        "sourceTitles": normalize_string_list(source_titles, limit=6, item_limit=100),
        "sourceDomains": normalize_string_list(source_domains, limit=6, item_limit=60),
    }
    filtered = [item for item in topics if isinstance(item, dict) and str(item.get("topicKey")) != entry["topicKey"]]
    filtered.insert(0, entry)
    memory["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    memory["topics"] = filtered[:80]
    write_json_file(HERMES_EVIDENCE_MEMORY_FILE, memory)
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
    else:
        topics = sorted(
            [item for item in topics if isinstance(item, dict)],
            key=lambda item: (
                {"critical": 0, "high": 1, "normal": 2, "low": 3}.get(str(item.get("lastPriority") or "normal"), 9),
                -float(item.get("trustScore", 0) or 0),
                str(item.get("lastSeenAt") or ""),
            ),
        )
    lines = ["Hermes evidence memory"]
    for item in topics[:4]:
        if not isinstance(item, dict):
            continue
        title = sanitize_text(item.get("title"), 90) or sanitize_text(item.get("topicKey"), 90)
        trust = item.get("trustScore")
        priority = sanitize_text(item.get("lastPriority"), 24)
        decision = sanitize_text(item.get("lastDecision"), 32)
        source_domains = normalize_string_list(item.get("sourceDomains"), limit=3, item_limit=60)
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
