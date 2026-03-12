from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from .orch_runtime_state import (
    CLIO_KNOWLEDGE_MEMORY_FILE,
    HERMES_EVIDENCE_MEMORY_FILE,
    has_meaningful_value,
    normalize_string_list,
    read_json_file,
    safe_float,
    sanitize_text,
    write_json_file,
)


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
