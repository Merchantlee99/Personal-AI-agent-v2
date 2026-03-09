from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ClioPipelineResult:
    note_type: str
    folder: str
    template_name: str
    title: str
    tags: list[str]
    project_links: list[str]
    moc_candidates: list[str]
    related_notes: list[str]
    source_urls: list[str]
    notebooklm_title: str
    notebooklm_summary: str
    source_language: str
    deepl_target_lang: str
    deepl_required: bool
    deepl_applied: bool
    draft_state: str
    classification_confidence: float
    frontmatter: dict[str, Any]
    claim_review_required: bool
    claim_review_id: str | None
    note_action: str
    update_target: str | None
    merge_candidates: list[str]
    update_target_path: str | None
    merge_candidate_paths: list[str]
    suggestion_score: float | None
    suggestion_reasons: list[str]


CLIO_OBSIDIAN_FORMAT_VERSION = "clio_obsidian_v2"
CLIO_STATUS_DEFAULT = "seed"
CLIO_DRAFT_STATE_DEFAULT = "draft"
NOTE_TYPE_DEFAULT = "knowledge"
TEMPLATE_FILE_BY_TYPE = {
    "study": "tpl-study.md",
    "article": "tpl-article.md",
    "paper": "tpl-paper.md",
    "knowledge": "tpl-knowledge.md",
    "writing": "tpl-writing.md",
    "skill": "tpl-skill.md",
}
URL_PATTERN = re.compile(r"https?://[^\s)>]+")


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        token = value.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped


def _truncate_text(value: str, max_len: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3].rstrip() + "..."


def _extract_tokens(value: str) -> list[str]:
    return re.findall(r"[0-9A-Za-z가-힣-]{3,}", value.lower())


def _slugify(value: str) -> str:
    compact = re.sub(r"\s+", "-", value.strip().lower())
    compact = re.sub(r"[^0-9a-z가-힣_-]+", "-", compact)
    compact = re.sub(r"-{2,}", "-", compact).strip("-")
    return compact or "note"


def _sanitize_file_stem(value: str) -> str:
    compact = re.sub(r"[\\/:*?\"<>|]+", "-", value).strip()
    compact = re.sub(r"\s+", " ", compact)
    compact = compact[:120].rstrip(" .")
    return compact or "untitled-note"


def _next_available_note_path(directory: Path, file_stem: str) -> Path:
    base = _sanitize_file_stem(file_stem)
    candidate = directory / f"{base}.md"
    if not candidate.exists():
        return candidate
    for index in range(2, 1000):
        candidate = directory / f"{base} ({index}).md"
        if not candidate.exists():
            return candidate
    return directory / f"{base}-{uuid.uuid4().hex[:8]}.md"


def _is_user_facing_note(note_path: Path, vault_dir: Path) -> bool:
    try:
        relative = note_path.relative_to(vault_dir)
    except ValueError:
        return False
    parts = relative.parts
    if not parts:
        return False
    top = parts[0]
    if top.startswith("."):
        return False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", top):
        return False
    if top in {"99-Templates", "99-System"}:
        return False
    stem = note_path.stem
    if stem in {"CLAUDE", "환영합니다!"}:
        return False
    if stem.startswith(("Clio Suggestion ", "Clio Merge ")):
        return False
    return True


def _yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return '""'
    if isinstance(value, (int, float)):
        return str(value)
    return _yaml_quote(str(value))


def _extract_bracket_field(message: str, field_name: str) -> str:
    patterns = (
        rf"\[{re.escape(field_name)}\]\s*(.+)$",
        rf"\[{re.escape(field_name)}\s*[:=]\s*([^\]]+)\]",
    )
    for line in message.splitlines():
        token = line.strip()
        if not token:
            continue
        for pattern in patterns:
            match = re.search(pattern, token, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
    return ""


def _extract_source_lines(message: str) -> list[str]:
    lines = []
    in_sources = False
    for raw in message.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.lower() == "[sources]":
            in_sources = True
            continue
        if in_sources:
            if stripped.startswith("[") and stripped.endswith("]"):
                break
            if stripped.startswith("-"):
                lines.append(stripped[1:].strip())
    return lines


def _strip_inline_bracket_fields(text: str) -> str:
    cleaned = re.sub(r"\[[A-Za-z0-9_-]+\s*[:=]\s*[^\]]+\]\s*", "", text)
    cleaned = re.sub(r"\[[A-Za-z0-9_-]+\]\s*", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def _meaningful_lines(message: str) -> list[str]:
    lines: list[str] = []
    for raw in message.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.lower() == "[sources]":
            break
        if stripped.startswith("- ") and "http" in stripped:
            continue
        cleaned = _strip_inline_bracket_fields(stripped)
        if not cleaned:
            continue
        lines.append(cleaned)
    return lines
