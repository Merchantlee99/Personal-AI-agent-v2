from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from .orch_runtime_state import (
    MINERVA_WORKING_MEMORY_FILE,
    has_meaningful_value,
    normalize_project_entry,
    normalize_string_list,
    read_json_file,
    sanitize_text,
)


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
    MINERVA_WORKING_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
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
