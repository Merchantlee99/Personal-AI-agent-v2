from __future__ import annotations

import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


@dataclass
class RoutingResult:
    agent_id: str
    fallback_used: bool


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
DEFAULT_TAG_TAXONOMY = {
    "domains": [
        {"tag": "domain/pm", "keywords": ["pm", "product", "기획", "프로덕트", "로드맵", "우선순위"]},
        {"tag": "domain/ai", "keywords": ["ai", "llm", "agent", "인공지능", "모델", "prompt"]},
        {"tag": "domain/sql", "keywords": ["sql", "sqld", "query", "database", "쿼리"]},
        {"tag": "domain/data-analytics", "keywords": ["ga4", "bigquery", "gtm", "analytics", "분석", "측정"]},
        {"tag": "domain/travel", "keywords": ["travel", "trip", "여행", "관광"]},
        {"tag": "domain/research", "keywords": ["paper", "research", "doi", "arxiv", "논문", "리서치"]},
        {"tag": "domain/writing", "keywords": ["writing", "draft", "blog", "linkedin", "threads", "글", "초안"]},
        {"tag": "domain/knowledge-management", "keywords": ["obsidian", "zettelkasten", "knowledge", "moc", "vault", "노트"]},
    ],
    "sources": [],
}
DEFAULT_PROJECT_REGISTRY = {"projects": []}
DEFAULT_MOC_REGISTRY = {"mocs": []}

URL_PATTERN = re.compile(r"https?://[^\s)>]+")


def parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    token = raw.strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def load_agent_ids() -> set[str]:
    config_candidates = []
    env_path = os.getenv("AGENT_CONFIG_PATH")
    if env_path:
        config_candidates.append(Path(env_path))
    config_candidates.extend(
        [
            Path("/app/config/agents.json"),
            Path(__file__).resolve().parent.parent / "config" / "agents.json",
            Path.cwd() / "config" / "agents.json",
            Path.cwd().parent / "config" / "agents.json",
        ]
    )

    loaded: dict[str, object] | None = None
    for candidate in config_candidates:
        if not candidate.is_file():
            continue
        try:
            loaded = json.loads(candidate.read_text(encoding="utf-8"))
            break
        except (OSError, json.JSONDecodeError):
            continue

    if loaded is None:
        raise RuntimeError("agents config not found or invalid. expected config/agents.json")

    config = loaded
    canonical_source = config.get("canonical_ids")
    if not isinstance(canonical_source, list):
        raise RuntimeError("agents config must include canonical_ids list")
    canonical = {str(item).strip().lower() for item in canonical_source if str(item).strip()}
    if not canonical:
        raise RuntimeError("agents config canonical_ids cannot be empty")

    return canonical


def _candidate_config_paths(file_name: str, env_name: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    if env_name:
        env_path = os.getenv(env_name)
        if env_path:
            candidates.append(Path(env_path))
    candidates.extend(
        [
            Path("/app/config") / file_name,
            Path(__file__).resolve().parent.parent / "config" / file_name,
            Path.cwd() / "config" / file_name,
            Path.cwd().parent / "config" / file_name,
        ]
    )
    return candidates


def _load_json_config(file_name: str, fallback: dict[str, Any], env_name: str | None = None) -> dict[str, Any]:
    for candidate in _candidate_config_paths(file_name, env_name):
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return dict(fallback)


CANONICAL_IDS = load_agent_ids()
TAG_TAXONOMY = _load_json_config("tag_taxonomy.json", DEFAULT_TAG_TAXONOMY)
PROJECT_REGISTRY = _load_json_config("project_registry.json", DEFAULT_PROJECT_REGISTRY)
MOC_REGISTRY = _load_json_config("moc_registry.json", DEFAULT_MOC_REGISTRY)


def normalize_agent_id(raw: str) -> RoutingResult:
    token = raw.strip().lower()
    if token in CANONICAL_IDS:
        return RoutingResult(agent_id=token, fallback_used=False)
    return RoutingResult(agent_id="minerva", fallback_used=True)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _shared_root() -> Path:
    return Path(os.getenv("SHARED_ROOT", "/app/shared_data"))


def _memory_dir() -> Path:
    path = _shared_root() / "shared_memory"
    ensure_dir(path)
    return path


def _read_json_file(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return fallback


def _write_json_file(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _default_clio_knowledge_memory() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tagTaxonomyVersion": str(TAG_TAXONOMY.get("schemaVersion", "1")),
        "projectRegistryVersion": str(PROJECT_REGISTRY.get("schemaVersion", "1")),
        "mocRegistryVersion": str(MOC_REGISTRY.get("schemaVersion", "1")),
        "projects": [],
        "mocs": [],
        "recentNotes": [],
        "dedupeCandidates": [],
    }


def _default_clio_claim_review_queue() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "items": [],
    }


def _create_clio_claim_review(
    *,
    note_title: str,
    topic_key: str,
    vault_file: str,
    source_urls: list[str],
    project_links: list[str],
    moc_candidates: list[str],
) -> str:
    queue_path = _memory_dir() / "clio_claim_review_queue.json"
    queue = _read_json_file(queue_path, _default_clio_claim_review_queue())
    if not isinstance(queue, dict):
        queue = _default_clio_claim_review_queue()
    items = queue.get("items")
    if not isinstance(items, list):
        items = []

    review_id = uuid.uuid4().hex[:12]
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    items.append(
        {
            "id": review_id,
            "status": "pending_user_review",
            "title": note_title,
            "topicKey": topic_key,
            "vaultFile": vault_file,
            "sourceUrls": source_urls[:6],
            "projectLinks": project_links[:4],
            "mocCandidates": moc_candidates[:4],
            "requestedAt": now,
        }
    )
    queue["updatedAt"] = now
    queue["items"] = items[-200:]
    _write_json_file(queue_path, queue)
    return review_id


def _update_clio_knowledge_memory(
    *,
    clio: ClioPipelineResult,
    vault_file: str,
    claim_review_required: bool,
    claim_review_id: str | None,
) -> None:
    memory_path = _memory_dir() / "clio_knowledge_memory.json"
    memory = _read_json_file(memory_path, _default_clio_knowledge_memory())
    if not isinstance(memory, dict):
        memory = _default_clio_knowledge_memory()

    recent_notes = memory.get("recentNotes")
    if not isinstance(recent_notes, list):
        recent_notes = []

    note_entry = {
        "title": clio.title,
        "type": clio.note_type,
        "folder": clio.folder,
        "templateName": clio.template_name,
        "vaultFile": vault_file,
        "tags": clio.tags[:8],
        "projectLinks": clio.project_links[:4],
        "mocCandidates": clio.moc_candidates[:4],
        "relatedNotes": clio.related_notes[:6],
        "sourceUrls": clio.source_urls[:6],
        "draftState": clio.draft_state,
        "claimReviewRequired": claim_review_required,
        "claimReviewId": claim_review_id,
        "noteAction": clio.note_action,
        "updateTarget": clio.update_target,
        "updateTargetPath": clio.update_target_path,
        "mergeCandidates": clio.merge_candidates[:3],
        "mergeCandidatePaths": clio.merge_candidate_paths[:3],
        "suggestionScore": clio.suggestion_score,
        "suggestionReasons": clio.suggestion_reasons[:3],
        "suggestionState": "pending" if clio.note_action != "create" else "",
        "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    recent_notes.append(note_entry)

    dedupe_candidates = memory.get("dedupeCandidates")
    if not isinstance(dedupe_candidates, list):
        dedupe_candidates = []
    dedupe_candidates.append(
        {
            "title": clio.title,
            "type": clio.note_type,
            "vaultFile": vault_file,
            "relatedNotes": clio.related_notes[:4],
        }
    )

    memory["updatedAt"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    memory["tagTaxonomyVersion"] = str(TAG_TAXONOMY.get("schemaVersion", "1"))
    memory["projectRegistryVersion"] = str(PROJECT_REGISTRY.get("schemaVersion", "1"))
    memory["mocRegistryVersion"] = str(MOC_REGISTRY.get("schemaVersion", "1"))
    memory["projects"] = _dedupe_preserve_order([item.get("name", "") for item in PROJECT_REGISTRY.get("projects", []) if isinstance(item, dict)])[:20]
    memory["mocs"] = _dedupe_preserve_order([item.get("link", "") for item in MOC_REGISTRY.get("mocs", []) if isinstance(item, dict)])[:20]
    memory["recentNotes"] = recent_notes[-50:]
    memory["dedupeCandidates"] = dedupe_candidates[-50:]
    _write_json_file(memory_path, memory)


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


def _yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return "\"\""
    if isinstance(value, (int, float)):
        return str(value)
    return _yaml_quote(str(value))


def _extract_bracket_field(message: str, field_name: str) -> str:
    prefix = f"[{field_name}]"
    for line in message.splitlines():
        token = line.strip()
        if not token.lower().startswith(prefix.lower()):
            continue
        return token[len(prefix) :].strip()
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


def _meaningful_lines(message: str) -> list[str]:
    lines: list[str] = []
    for raw in message.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.lower() == "[sources]":
            break
        if stripped.startswith("[") and "]" in stripped[:32]:
            continue
        if stripped.startswith("- ") and "http" in stripped:
            continue
        lines.append(stripped)
    return lines


def _derive_title(message: str) -> str:
    bracket_title = _extract_bracket_field(message, "title")
    if bracket_title:
        return _truncate_text(bracket_title, 96)
    for line in _meaningful_lines(message):
        compact = re.sub(r"^(다음 내용을|요청:)\s*", "", line).strip(" -:")
        if compact:
            return _truncate_text(compact, 96)
    return "Clio Draft"


def _derive_summary(message: str) -> str:
    for line in _meaningful_lines(message):
        if len(line) >= 8:
            return _truncate_text(line, 220)
    return _truncate_text(" ".join(_extract_tokens(message)), 220)


def _extract_source_urls(message: str) -> list[str]:
    urls = URL_PATTERN.findall(message)
    return _dedupe_preserve_order(urls)[:8]


def _source_tag_from_url(url: str) -> str | None:
    try:
        host = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return None
    for entry in TAG_TAXONOMY.get("sources", []):
        if not isinstance(entry, dict):
            continue
        tag = str(entry.get("tag", "")).strip()
        hosts = entry.get("hosts")
        if not tag or not isinstance(hosts, list):
            continue
        if any(host == str(item).strip().lower() for item in hosts):
            return tag
    if not host:
        return None
    parts = [part for part in host.split(".") if part]
    label = parts[-2] if len(parts) >= 2 else parts[0]
    return f"source/{_slugify(label)}"


def _infer_note_type(message: str, source_urls: list[str], source: str) -> tuple[str, float]:
    lowered = message.lower()
    scores = {token: 0.0 for token in TEMPLATE_FILE_BY_TYPE}

    if any(keyword in lowered for keyword in ("sqld", "gaiq", "자격증", "기출", "시험", "chapter", "챕터")):
        scores["study"] += 0.85
    if any(keyword in lowered for keyword in ("framework", "methodology", "softskill", "skill", "프레임워크", "방법론", "스킬")):
        scores["skill"] += 0.8
    if any(keyword in lowered for keyword in ("draft", "blog", "linkedin", "threads", "publish", "글", "초안", "퇴고")):
        scores["writing"] += 0.8
    if any(keyword in lowered for keyword in ("paper", "research", "doi", "arxiv", "researchgate", "논문", "저널", "학회")):
        scores["paper"] += 0.9
    if any(keyword in lowered for keyword in ("insight", "원칙", "깨달음", "회고", "왜 이렇게 생각", "핵심 주장")):
        scores["knowledge"] += 0.75

    if source_urls:
        scores["article"] += 0.55
        if any(("arxiv.org" in url or "researchgate.net" in url or "/doi/" in url) for url in source_urls):
            scores["paper"] += 0.55

    if source == "agent-followup":
        scores["knowledge"] += 0.2
    if source == "telegram-inline-action" and source_urls:
        scores["article"] += 0.15

    note_type, confidence = max(scores.items(), key=lambda item: item[1])
    if confidence <= 0:
        return NOTE_TYPE_DEFAULT, 0.55
    return note_type, round(min(0.98, max(0.55, confidence)), 2)


def _infer_domain_tags(message: str, note_type: str) -> list[str]:
    lowered = message.lower()
    tags: list[str] = []
    for entry in TAG_TAXONOMY.get("domains", []):
        if not isinstance(entry, dict):
            continue
        tag = str(entry.get("tag", "")).strip()
        keywords = entry.get("keywords")
        if not tag or not isinstance(keywords, list):
            continue
        if any(str(keyword).lower() in lowered for keyword in keywords):
            tags.append(tag)
    if note_type == "skill" and "domain/pm" not in tags:
        tags.append("domain/pm")
    return _dedupe_preserve_order(tags)[:4]


def _match_projects(message: str) -> list[dict[str, str]]:
    lowered = message.lower()
    matches: list[dict[str, str]] = []
    for entry in PROJECT_REGISTRY.get("projects", []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        slug = str(entry.get("slug", "")).strip()
        aliases = entry.get("aliases")
        folder = str(entry.get("folder", "")).strip()
        moc = str(entry.get("moc", "")).strip()
        if not name or not slug or not isinstance(aliases, list):
            continue
        sanitized_aliases = []
        for alias in aliases:
            token = str(alias).strip().lower()
            if not token or token in CANONICAL_IDS:
                continue
            sanitized_aliases.append(token)
        if any(token in lowered for token in sanitized_aliases):
            matches.append({"name": name, "slug": slug, "folder": folder, "moc": moc})
    return matches[:3]


def _infer_moc_candidates(note_type: str, domain_tags: list[str], projects: list[dict[str, str]]) -> list[str]:
    project_slugs = {item.get("slug", "") for item in projects}
    candidates: list[str] = [item.get("moc", "") for item in projects if item.get("moc")]
    for entry in MOC_REGISTRY.get("mocs", []):
        if not isinstance(entry, dict):
            continue
        link = str(entry.get("link", "")).strip()
        if not link:
            continue
        types = entry.get("types")
        domains = entry.get("domains")
        entry_projects = entry.get("projects")
        type_match = isinstance(types, list) and note_type in {str(item).strip() for item in types}
        domain_match = isinstance(domains, list) and any(tag in {str(item).strip() for item in domains} for tag in domain_tags)
        project_match = isinstance(entry_projects, list) and any(slug in {str(item).strip() for item in entry_projects} for slug in project_slugs)
        generic_type_match = type_match and not isinstance(domains, list) and not isinstance(entry_projects, list)
        if project_match or domain_match or generic_type_match:
            candidates.append(link)
    return _dedupe_preserve_order([item for item in candidates if item])[:5]


def _find_recent_note_links(message: str, vault_dir: Path, *, extra_links: list[str] | None = None) -> list[str]:
    tokens = _extract_tokens(message)
    related: list[str] = list(extra_links or [])
    for note_path in sorted(vault_dir.rglob("*.md"), reverse=True)[:60]:
        stem = note_path.stem
        if stem.startswith("index") or stem.startswith("tpl-"):
            continue
        lowered = stem.lower()
        if any(token in lowered for token in tokens):
            related.append(f"[[{stem}]]")
        if len(related) >= 8:
            break
    return _dedupe_preserve_order(related)[:8]


def _infer_note_reuse_strategy(
    title: str,
    message: str,
    vault_dir: Path,
) -> tuple[str, str | None, str | None, list[str], list[str], float | None, list[str]]:
    title_slug = _slugify(title)
    title_tokens = set(_extract_tokens(title))
    message_tokens = set(_extract_tokens(message))
    candidate_scores: list[tuple[float, str, str, list[str]]] = []

    for note_path in sorted(vault_dir.rglob("*.md"), reverse=True)[:120]:
        stem = note_path.stem
        if stem.startswith("index") or stem.startswith("tpl-"):
            continue
        stem_slug = _slugify(stem)
        shared_relative_path = note_path.relative_to(vault_dir.parent).as_posix()
        if stem_slug == title_slug:
            shared_tokens = sorted((title_tokens & message_tokens) or title_tokens)[:3]
            reasons = [
                "제목이 기존 노트와 정확히 일치합니다.",
                "같은 주제의 기존 노트를 갱신하는 편이 중복 노트 생성보다 적합합니다.",
            ]
            if shared_tokens:
                reasons.append(f"핵심 토큰이 겹칩니다: {', '.join(shared_tokens)}")
            return "update_candidate", f"[[{stem}]]", shared_relative_path, [], [], 0.98, reasons[:3]

        stem_tokens = set(_extract_tokens(stem))
        if not stem_tokens:
            continue
        overlap_title = len(title_tokens & stem_tokens) / max(1, len(title_tokens | stem_tokens)) if title_tokens else 0.0
        overlap_message = len(message_tokens & stem_tokens) / max(1, len(stem_tokens)) if message_tokens else 0.0
        score = max(overlap_title, overlap_message * 0.6)
        if score >= 0.34:
            shared_title_tokens = sorted(title_tokens & stem_tokens)[:3]
            shared_message_tokens = sorted(message_tokens & stem_tokens)[:3]
            reasons: list[str] = []
            if shared_title_tokens:
                reasons.append(f"제목 핵심 토큰이 겹칩니다: {', '.join(shared_title_tokens)}")
            if shared_message_tokens:
                reasons.append(f"본문 문맥 토큰이 겹칩니다: {', '.join(shared_message_tokens)}")
            reasons.append("새 노트를 따로 만드는 것보다 기존 노트와의 update/merge 검토 가치가 높습니다.")
            candidate_scores.append((score, stem, shared_relative_path, reasons[:3]))

    candidate_scores.sort(key=lambda item: (-item[0], item[1].lower()))
    merge_candidates = [f"[[{stem}]]" for _, stem, _, _ in candidate_scores[:3]]
    merge_candidate_paths = [path for _, _, path, _ in candidate_scores[:3]]
    if merge_candidates:
        top_score, _, _, top_reasons = candidate_scores[0]
        reasons = list(top_reasons)
        if len(merge_candidates) > 1:
            reasons.append(f"서로 가까운 후보가 {len(merge_candidates)}개 있어 병합 검토가 적합합니다.")
        return "merge_candidate", None, None, merge_candidates, merge_candidate_paths, round(top_score, 2), reasons[:3]
    return "create", None, None, [], [], None, []


def _route_folder(note_type: str, projects: list[dict[str, str]]) -> str:
    if projects and projects[0].get("folder"):
        return str(projects[0]["folder"]).strip().rstrip("/")
    return {
        "study": "01-Knowledge",
        "article": "02-References",
        "paper": "02-References",
        "knowledge": "01-Knowledge",
        "writing": "04-Writing",
        "skill": "01-Knowledge",
    }.get(note_type, "00-Inbox")


def _render_frontmatter(frontmatter: dict[str, Any]) -> list[str]:
    lines = ["---"]
    ordered_keys = [
        "clio_format_version",
        "title",
        "type",
        "tags",
        "status",
        "created",
        "updated",
        "source_type",
        "source_url",
        "project_links",
        "moc_candidates",
        "draft_state",
        "template_name",
        "classification_confidence",
        "note_action",
        "update_target",
        "update_target_path",
        "merge_candidates",
        "merge_candidate_paths",
        "suggestion_score",
        "suggestion_reasons",
    ]
    for key in ordered_keys:
        if key not in frontmatter:
            continue
        value = frontmatter[key]
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
                continue
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_yaml_scalar(item)}")
            continue
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return lines


def _render_article_sections(title: str, summary: str, source_lines: list[str], related_notes: list[str], projects: list[str]) -> list[str]:
    point_three = "PM 관점에서 실제 제품/지표에 어떤 변화가 생기는지 검토 필요"
    if projects:
        point_three = f"{projects[0]}와 연결되는 시사점 검토 필요"
    return [
        "## 한 줄 요약",
        summary,
        "",
        "## 3가지 핵심 포인트",
        f"1. **핵심 주장** — {summary}",
        "2. **근거 확인** — 원문/출처 세부 검토가 추가로 필요합니다.",
        f"3. **적용 포인트** — {point_three}",
        "",
        "## 인상 깊은 구절 / 데이터",
        f"> {_truncate_text(source_lines[0] if source_lines else summary, 180)}",
        "",
        "## 나의 생각",
        "PM 관점의 시사점은 사용자 검토 후 확정합니다.",
        "",
        "## 액션 아이템",
        "- [ ] 핵심 주장과 실제 적용 가능성을 검토한다.",
        "- [ ] 관련 프로젝트에 반영할 항목을 정한다.",
        "",
        "## 연결 노트",
        *( [f"- 관련 노트: {item}" for item in related_notes[:3]] if related_notes else ["- 관련 노트: 사용자 검토 필요"] ),
    ]


def _render_paper_sections(summary: str, source_lines: list[str], related_notes: list[str], projects: list[str]) -> list[str]:
    apply_line = f"{projects[0]}에 적용 가능성 검토" if projects else "현재 프로젝트에 적용 가능성 검토"
    return [
        "## 연구 질문",
        summary,
        "",
        "## 방법론",
        "원문 상세 검토 후 보강 필요",
        "",
        "## 핵심 결과",
        "| 지표 | 수치/결과 |",
        "|------|-----------|",
        f"| 요약 | {summary} |",
        "",
        "## 한계점",
        "- 원문 전체 검토 전까지는 해석 보류",
        "",
        "## 내 업무에의 적용",
        "> [!apply] PM/프로덕트 적용 포인트",
        f"> {apply_line}",
        "",
        "## 연결 노트",
        *( [f"- 관련 노트: {item}" for item in related_notes[:3]] if related_notes else ["- 관련 노트: 사용자 검토 필요"] ),
        *( [f"- 원문 힌트: {source_lines[0]}"] if source_lines else [] ),
    ]


def _render_study_sections(summary: str, related_notes: list[str]) -> list[str]:
    return [
        "## 핵심 개념",
        summary,
        "",
        "## 상세 내용",
        "",
        "### 정의",
        f"- {summary}",
        "",
        "### 작동 원리 / 구조",
        "- 사용자 검토 후 보강",
        "",
        "### 주의할 점",
        "- 시험/실전 함정은 추가 정리 필요",
        "",
        "## 기출 & 실전 포인트",
        "",
        "> [!exam] 시험 출제 포인트",
        "> 자주 헷갈리는 정의와 예외를 보강하세요.",
        "",
        "## 연결 노트",
        *( [f"- 관련 노트: {item}" for item in related_notes[:3]] if related_notes else ["- 관련 노트: 사용자 검토 필요"] ),
        "",
        "## 나만의 정리 (한 줄 요약)",
        "",
        "> 사용자 검토 후 일상 언어 요약 추가",
    ]


def _render_skill_sections(summary: str, related_notes: list[str], projects: list[str]) -> list[str]:
    apply_target = ", ".join(projects[:2]) if projects else "현재 프로젝트"
    return [
        "## 한 줄 정의",
        summary,
        "",
        "## 언제 사용하는가",
        "구체적 시나리오는 사용자 검토 후 보강",
        "",
        "## 핵심 구성 요소",
        "| 요소 | 설명 |",
        "|------|------|",
        f"| 핵심 | {summary} |",
        "",
        "## 적용 사례",
        "",
        "### 교과서적 사례",
        "대표 사례 보강 필요",
        "",
        "### 내 프로젝트 적용",
        f"{apply_target}에 어떻게 적용할지 검토",
        "",
        "## 실전 팁",
        "",
        "> [!tip] 실무에서 주의할 점",
        "> 추상 설명으로 끝내지 말고 실제 프로젝트 행동으로 연결하세요.",
        "",
        "## 관련 스킬",
        *( [f"- 관련 노트: {item}" for item in related_notes[:3]] if related_notes else ["- 관련 노트: 사용자 검토 필요"] ),
    ]


def _render_knowledge_sections(summary: str, related_notes: list[str]) -> list[str]:
    return [
        "## 핵심 주장",
        summary,
        "",
        "## 왜 이렇게 생각하는가",
        "근거와 경험은 사용자 검토 후 보강",
        "",
        "## 구체적 사례",
        "- 사례 1: 사용자 검토 필요",
        "- 사례 2: 사용자 검토 필요",
        "",
        "## 반론 / 예외",
        "이 주장이 틀릴 수 있는 조건을 보강하세요.",
        "",
        "## 연결 노트",
        *( [f"- 관련 노트: {item}" for item in related_notes[:3]] if related_notes else ["- 관련 노트: 사용자 검토 필요"] ),
    ]


def _render_writing_sections(summary: str, related_notes: list[str], projects: list[str]) -> list[str]:
    audience_hint = projects[0] if projects else "타겟 독자"
    return [
        "## 핵심 메시지 (1문장)",
        summary,
        "",
        "## 아웃라인",
        "1. **도입** — 문제를 왜 지금 읽어야 하는지 제시",
        "2. **본론 1** — 핵심 주장",
        "3. **본론 2** — 사례 또는 근거",
        "4. **본론 3** — 적용 또는 관점 차이",
        "5. **결론** — 다음 행동/CTA",
        "",
        "## 초안",
        f"{audience_hint} 기준으로 초안을 확장하세요.",
        "",
        "## 퇴고 체크리스트",
        "- [ ] 첫 문장이 충분히 강한가?",
        "- [ ] 불필요한 설명을 줄였는가?",
        "- [ ] 사례나 데이터가 포함됐는가?",
        "",
        "## 참고 자료",
        *( [f"- {item}" for item in related_notes[:3]] if related_notes else ["- 사용자 검토 필요"] ),
    ]


def _render_note_sections(
    note_type: str,
    title: str,
    summary: str,
    source_lines: list[str],
    related_notes: list[str],
    project_names: list[str],
) -> list[str]:
    if note_type == "article":
        return _render_article_sections(title, summary, source_lines, related_notes, project_names)
    if note_type == "paper":
        return _render_paper_sections(summary, source_lines, related_notes, project_names)
    if note_type == "study":
        return _render_study_sections(summary, related_notes)
    if note_type == "skill":
        return _render_skill_sections(summary, related_notes, project_names)
    if note_type == "writing":
        return _render_writing_sections(summary, related_notes, project_names)
    return _render_knowledge_sections(summary, related_notes)


def _normalize_language(value: str) -> str:
    token = re.sub(r"[^A-Za-z]", "", value).lower()
    if not token:
        return "unknown"
    return token


def detect_source_language(message: str) -> str:
    explicit_patterns = (
        r"source[_\s-]?lang(?:uage)?\s*[:=]\s*([A-Za-z-]{2,10})",
        r"\[(?:source[_\s-]?lang(?:uage)?)\s*[:=]\s*([A-Za-z-]{2,10})\]",
    )
    for pattern in explicit_patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return _normalize_language(match.group(1))

    if re.search(r"[가-힣]", message):
        return "ko"
    if re.search(r"[ぁ-んァ-ン一-龥]", message):
        return "ja"
    return "en"


def translate_with_deepl(text: str, source_language: str, target_language: str) -> str | None:
    api_key = os.getenv("DEEPL_API_KEY", "").strip()
    if not api_key:
        return None

    payload: list[tuple[str, str]] = [("text", text), ("target_lang", target_language.upper())]
    source_token = _normalize_language(source_language).upper()
    if source_token not in {"", "UNKNOWN", "AUTO"}:
        payload.append(("source_lang", source_token))

    glossary_id = os.getenv("DEEPL_GLOSSARY_ID", "").strip()
    if glossary_id:
        payload.append(("glossary_id", glossary_id))

    request = urllib.request.Request(
        "https://api-free.deepl.com/v2/translate",
        data=urllib.parse.urlencode(payload, doseq=True).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"DeepL-Auth-Key {api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=8) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None

    translations = body.get("translations")
    if not isinstance(translations, list) or not translations:
        return None
    first = translations[0]
    if not isinstance(first, dict):
        return None
    translated = str(first.get("text", "")).strip()
    return translated or None


def infer_clio_pipeline(message: str, vault_dir: Path, source: str) -> ClioPipelineResult:
    note_type, classification_confidence = _infer_note_type(message, _extract_source_urls(message), source)
    source_urls = _extract_source_urls(message)
    title = _derive_title(message)
    summary = _derive_summary(message)
    source_lines = _extract_source_lines(message)
    projects = _match_projects(message)
    folder = _route_folder(note_type, projects)
    template_name = TEMPLATE_FILE_BY_TYPE.get(note_type, TEMPLATE_FILE_BY_TYPE[NOTE_TYPE_DEFAULT])
    domain_tags = _infer_domain_tags(message, note_type)
    project_links = [f"[[{item['name']}]]" for item in projects if item.get("name")]
    moc_candidates = _infer_moc_candidates(note_type, domain_tags, projects)
    source_tags = [tag for tag in (_source_tag_from_url(url) for url in source_urls[:2]) if tag]
    tags = _dedupe_preserve_order(
        [
            f"type/{note_type}",
            *domain_tags,
            *source_tags,
            *(f"project/{item['slug']}" for item in projects if item.get("slug")),
            "status/seed",
        ]
    )
    related_notes = _find_recent_note_links(message, vault_dir, extra_links=[*project_links, *moc_candidates])
    (
        note_action,
        update_target,
        update_target_path,
        merge_candidates,
        merge_candidate_paths,
        suggestion_score,
        suggestion_reasons,
    ) = _infer_note_reuse_strategy(title, message, vault_dir)
    related_notes = _dedupe_preserve_order([*related_notes, *merge_candidates])

    normalized_source = source.strip().lower()
    source_type = "user"
    trigger = _extract_bracket_field(message, "trigger").lower()
    if "minerva" in trigger or normalized_source == "agent-followup":
        source_type = "minerva"
    elif (
        normalized_source in {"orchestration-event", "telegram-inline-action"}
        or "hermes" in trigger
        or normalized_source.startswith("hermes")
    ):
        source_type = "hermes"
    elif source_urls and normalized_source in {"unit-test", "integration-test"}:
        source_type = "hermes"

    frontmatter = {
        "clio_format_version": CLIO_OBSIDIAN_FORMAT_VERSION,
        "title": title,
        "type": note_type,
        "tags": tags,
        "status": CLIO_STATUS_DEFAULT,
        "created": datetime.now(UTC).strftime("%Y-%m-%d"),
        "updated": datetime.now(UTC).strftime("%Y-%m-%d"),
        "source_type": source_type,
        "source_url": source_urls[0] if source_urls else "",
        "project_links": project_links,
        "moc_candidates": moc_candidates,
        "draft_state": CLIO_DRAFT_STATE_DEFAULT,
        "template_name": template_name,
        "classification_confidence": classification_confidence,
        "note_action": note_action,
        "update_target": update_target or "",
        "update_target_path": update_target_path or "",
        "merge_candidates": merge_candidates,
        "merge_candidate_paths": merge_candidate_paths,
        "suggestion_score": suggestion_score if suggestion_score is not None else "",
        "suggestion_reasons": suggestion_reasons,
    }

    notebooklm_title = _truncate_text(title, 72)
    source_language = detect_source_language(message)
    deepl_target_lang = re.sub(r"[^A-Za-z]", "", os.getenv("DEEPL_TARGET_LANG", "KO")).upper() or "KO"
    source_token = _normalize_language(source_language).upper()
    deepl_required = source_token not in {"", "UNKNOWN"} and source_token != deepl_target_lang

    notebooklm_summary = _truncate_text(summary, 240)
    deepl_applied = False
    if deepl_required:
        translated_summary = translate_with_deepl(notebooklm_summary, source_language, deepl_target_lang)
        if translated_summary:
            notebooklm_summary = _truncate_text(translated_summary, 240)
            deepl_applied = True

    return ClioPipelineResult(
        note_type=note_type,
        folder=folder,
        template_name=template_name,
        title=title,
        tags=tags,
        project_links=project_links,
        moc_candidates=moc_candidates,
        related_notes=related_notes,
        source_urls=source_urls,
        notebooklm_title=notebooklm_title,
        notebooklm_summary=notebooklm_summary,
        source_language=source_language,
        deepl_target_lang=deepl_target_lang,
        deepl_required=deepl_required,
        deepl_applied=deepl_applied,
        draft_state=CLIO_DRAFT_STATE_DEFAULT,
        classification_confidence=classification_confidence,
        frontmatter=frontmatter,
        claim_review_required=(note_type == "knowledge"),
        claim_review_id=None,
        note_action=note_action,
        update_target=update_target,
        merge_candidates=merge_candidates,
        update_target_path=update_target_path,
        merge_candidate_paths=merge_candidate_paths,
        suggestion_score=suggestion_score,
        suggestion_reasons=suggestion_reasons,
    )


def dispatch_notebooklm_sync(payload: dict[str, object]) -> dict[str, object]:
    enabled = parse_bool_env("NOTEBOOKLM_SYNC_ENABLED", False)
    if not enabled:
        return {"attempted": False, "delivered": False, "reason": "disabled"}

    endpoint = os.getenv("NOTEBOOKLM_INGEST_WEBHOOK_URL", "").strip()
    if not endpoint:
        return {"attempted": False, "delivered": False, "reason": "missing_endpoint"}

    timeout_raw = os.getenv("NOTEBOOKLM_TIMEOUT_SEC", "8").strip()
    try:
        timeout_sec = max(1.0, float(timeout_raw))
    except ValueError:
        timeout_sec = 8.0

    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("NOTEBOOKLM_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = {
        "source": "nanoclaw-clio",
        "generated_at": payload.get("generated_at"),
        "agent_id": payload.get("agent_id"),
        "title": (payload.get("notebooklm") or {}).get("title"),
        "summary": (payload.get("notebooklm") or {}).get("summary"),
        "vault_file": (payload.get("notebooklm") or {}).get("vault_file"),
        "tags": payload.get("tags", []),
        "source_urls": payload.get("source_urls", []),
    }

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:  # noqa: S310
            status = int(getattr(response, "status", 0) or 0)
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"attempted": True, "delivered": False, "reason": "request_failed"}

    if 200 <= status < 300:
        return {"attempted": True, "delivered": True, "reason": "ok", "status": status}
    return {"attempted": True, "delivered": False, "reason": "non_2xx", "status": status}


def parse_payload(path: Path) -> dict[str, str]:
    raw_content = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        parsed = json.loads(raw_content)
        return {
            "agent_id": str(parsed.get("agent_id", "")).strip(),
            "message": str(parsed.get("message", "")).strip(),
            "source": str(parsed.get("source", "file_bus")).strip(),
        }

    lines = [line.strip() for line in raw_content.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("text payload must include agent_id and message lines")
    return {"agent_id": lines[0], "message": "\n".join(lines[1:]), "source": "file_bus"}


def build_markdown(
    agent_id: str,
    source: str,
    message: str,
    fallback_used: bool,
    clio: ClioPipelineResult | None = None,
) -> str:
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    lines = []
    if clio is not None:
        frontmatter = dict(clio.frontmatter)
        frontmatter["source_url"] = clio.source_urls[0] if clio.source_urls else ""
        lines.extend(_render_frontmatter(frontmatter))
        lines.append("")
        lines.extend(
            _render_note_sections(
                clio.note_type,
                clio.title,
                clio.notebooklm_summary,
                _extract_source_lines(message),
                clio.related_notes,
                [item.strip("[]") for item in clio.project_links],
            )
        )
    else:
        lines.extend(
            [
                "---",
                f"agent_id: {_yaml_quote(agent_id)}",
                f"timestamp: {_yaml_quote(timestamp)}",
                f"source: {_yaml_quote(source)}",
                f"fallback_used: {str(fallback_used).lower()}",
                "---",
                "",
                f"# NanoClaw Inbox Capture ({agent_id})",
                "",
                "## Message",
                message,
            ]
        )
    if clio is not None:
        lines.extend(
            [
                "",
                "## Clio Metadata",
                f"- format_version: {CLIO_OBSIDIAN_FORMAT_VERSION}",
                f"- note_type: {clio.note_type}",
                f"- folder: {clio.folder}",
                f"- template_name: {clio.template_name}",
                f"- draft_state: {clio.draft_state}",
                f"- classification_confidence: {clio.classification_confidence}",
                f"- claim_review_required: {str(clio.claim_review_required).lower()}",
                f"- claim_review_id: {clio.claim_review_id or '없음'}",
                f"- note_action: {clio.note_action}",
                f"- update_target: {clio.update_target or '없음'}",
                f"- update_target_path: {clio.update_target_path or '없음'}",
                f"- merge_candidates: {', '.join(clio.merge_candidates) if clio.merge_candidates else '없음'}",
                f"- merge_candidate_paths: {', '.join(clio.merge_candidate_paths) if clio.merge_candidate_paths else '없음'}",
                f"- suggestion_score: {clio.suggestion_score if clio.suggestion_score is not None else '없음'}",
                f"- tags: {', '.join(clio.tags)}",
                f"- source_language: {clio.source_language}",
                f"- source_urls: {', '.join(clio.source_urls) if clio.source_urls else '없음'}",
                f"- deepl_required: {str(clio.deepl_required).lower()}",
                f"- deepl_applied: {str(clio.deepl_applied).lower()}",
                f"- notebooklm_title: {clio.notebooklm_title}",
                f"- notebooklm_ready: true",
                "",
                "## Clio Relationships",
            ]
        )
        if clio.project_links:
            lines.extend([f"- project: {item}" for item in clio.project_links])
        if clio.moc_candidates:
            lines.extend([f"- moc: {item}" for item in clio.moc_candidates])
        if clio.related_notes:
            lines.extend([f"- related: {item}" for item in clio.related_notes])
        else:
            lines.append("- 없음")
        if clio.suggestion_reasons:
            lines.extend([f"- suggestion_reason: {item}" for item in clio.suggestion_reasons[:3]])
        lines.extend(
            [
                "",
                "## NotebookLM Summary",
                clio.notebooklm_summary,
            ]
        )
    lines.extend(
        [
            "",
            "## Routing Rules",
            "- canonical ids only: minerva/clio/hermes",
            "- aliases disabled: use canonical ids only",
            "- unknown agent fallback: minerva",
        ]
    )
    return "\n".join(lines)


def process_file(
    path: Path,
    inbox_dir: Path,
    outbox_dir: Path,
    archive_dir: Path,
    vault_dir: Path,
    verified_inbox_dir: Path,
) -> None:
    if not path.exists() or not path.is_file():
        return

    payload = parse_payload(path)
    if not payload["message"]:
        raise ValueError("message is required")

    routing = normalize_agent_id(payload["agent_id"])
    now = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")

    clio_pipeline: ClioPipelineResult | None = None
    if routing.agent_id == "clio":
        clio_pipeline = infer_clio_pipeline(payload["message"], vault_dir, payload["source"])

    markdown = build_markdown(
        agent_id=routing.agent_id,
        source=payload["source"],
        message=payload["message"],
        fallback_used=routing.fallback_used,
        clio=clio_pipeline,
    )
    vault_target_dir = vault_dir / clio_pipeline.folder if clio_pipeline is not None else vault_dir / datetime.now(UTC).strftime("%Y-%m-%d")
    ensure_dir(vault_target_dir)
    file_stem = _sanitize_file_stem(clio_pipeline.title if clio_pipeline is not None else routing.agent_id)
    vault_path = vault_target_dir / f"{now}-{file_stem}.md"
    vault_path.write_text(markdown, encoding="utf-8")
    shared_relative_vault_path = vault_path.relative_to(vault_dir.parent).as_posix()

    verified_relative_path: str | None = None
    notebooklm_dispatch: dict[str, object] = {"attempted": False, "delivered": False, "reason": "not_clio"}
    if clio_pipeline is not None:
        topic_key = _extract_bracket_field(payload["message"], "topic") or _slugify(clio_pipeline.title)
        if clio_pipeline.claim_review_required:
            clio_pipeline.claim_review_id = _create_clio_claim_review(
                note_title=clio_pipeline.title,
                topic_key=topic_key,
                vault_file=shared_relative_vault_path,
                source_urls=clio_pipeline.source_urls,
                project_links=clio_pipeline.project_links,
                moc_candidates=clio_pipeline.moc_candidates,
            )
        _update_clio_knowledge_memory(
            clio=clio_pipeline,
            vault_file=shared_relative_vault_path,
            claim_review_required=clio_pipeline.claim_review_required,
            claim_review_id=clio_pipeline.claim_review_id,
        )
        verified_payload = {
            "agent_id": routing.agent_id,
            "format_version": CLIO_OBSIDIAN_FORMAT_VERSION,
            "source": payload["source"],
            "message": payload["message"],
            "type": clio_pipeline.note_type,
            "folder": clio_pipeline.folder,
            "title": clio_pipeline.title,
            "template_name": clio_pipeline.template_name,
            "draft_state": clio_pipeline.draft_state,
            "classification_confidence": clio_pipeline.classification_confidence,
            "tags": clio_pipeline.tags,
            "project_links": clio_pipeline.project_links,
            "moc_candidates": clio_pipeline.moc_candidates,
            "related_notes": clio_pipeline.related_notes,
            "note_action": clio_pipeline.note_action,
            "update_target": clio_pipeline.update_target,
            "update_target_path": clio_pipeline.update_target_path,
            "merge_candidates": clio_pipeline.merge_candidates,
            "merge_candidate_paths": clio_pipeline.merge_candidate_paths,
            "suggestion_score": clio_pipeline.suggestion_score,
            "suggestion_reasons": clio_pipeline.suggestion_reasons,
            "claim_review_required": clio_pipeline.claim_review_required,
            "claim_review_id": clio_pipeline.claim_review_id,
            "source_urls": clio_pipeline.source_urls,
            "source_language": clio_pipeline.source_language,
            "frontmatter": clio_pipeline.frontmatter,
            "deepl": {
                "target_lang": clio_pipeline.deepl_target_lang,
                "required": clio_pipeline.deepl_required,
                "applied": clio_pipeline.deepl_applied,
            },
            "notebooklm": {
                "ready": True,
                "title": clio_pipeline.notebooklm_title,
                "summary": clio_pipeline.notebooklm_summary,
                "vault_file": shared_relative_vault_path,
            },
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        verified_path = verified_inbox_dir / f"{now}-{path.stem}.json"
        verified_path.write_text(json.dumps(verified_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        verified_relative_path = verified_path.relative_to(vault_dir.parent).as_posix()
        notebooklm_dispatch = dispatch_notebooklm_sync(verified_payload)

    outbox_payload = {
        "agent_id": routing.agent_id,
        "format_version": CLIO_OBSIDIAN_FORMAT_VERSION if clio_pipeline else None,
        "fallback_used": routing.fallback_used,
        "input_file": path.name,
        "vault_file": shared_relative_vault_path,
        "vault_file_container": str(vault_path),
        "type": clio_pipeline.note_type if clio_pipeline else None,
        "folder": clio_pipeline.folder if clio_pipeline else None,
        "title": clio_pipeline.title if clio_pipeline else None,
        "template_name": clio_pipeline.template_name if clio_pipeline else None,
        "draft_state": clio_pipeline.draft_state if clio_pipeline else None,
        "classification_confidence": clio_pipeline.classification_confidence if clio_pipeline else None,
        "note_action": clio_pipeline.note_action if clio_pipeline else None,
        "update_target": clio_pipeline.update_target if clio_pipeline else None,
        "update_target_path": clio_pipeline.update_target_path if clio_pipeline else None,
        "merge_candidates": clio_pipeline.merge_candidates if clio_pipeline else [],
        "merge_candidate_paths": clio_pipeline.merge_candidate_paths if clio_pipeline else [],
        "suggestion_score": clio_pipeline.suggestion_score if clio_pipeline else None,
        "suggestion_reasons": clio_pipeline.suggestion_reasons if clio_pipeline else [],
        "claim_review_required": clio_pipeline.claim_review_required if clio_pipeline else False,
        "claim_review_id": clio_pipeline.claim_review_id if clio_pipeline else None,
        "tags": clio_pipeline.tags if clio_pipeline else [],
        "project_links": clio_pipeline.project_links if clio_pipeline else [],
        "moc_candidates": clio_pipeline.moc_candidates if clio_pipeline else [],
        "related_notes": clio_pipeline.related_notes if clio_pipeline else [],
        "source_urls": clio_pipeline.source_urls if clio_pipeline else [],
        "source_language": clio_pipeline.source_language if clio_pipeline else None,
        "deepl_target_lang": clio_pipeline.deepl_target_lang if clio_pipeline else None,
        "deepl_required": clio_pipeline.deepl_required if clio_pipeline else False,
        "deepl_applied": clio_pipeline.deepl_applied if clio_pipeline else False,
        "notebooklm_ready": clio_pipeline is not None,
        "notebooklm_dispatch": notebooklm_dispatch,
        "verified_file": verified_relative_path,
        "processed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    outbox_path = outbox_dir / f"{now}-{path.stem}.json"
    outbox_path.write_text(json.dumps(outbox_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    followup_minerva_inbox: str | None = None
    deep_dive_followup_enabled = parse_bool_env("HERMES_DEEP_DIVE_AUTO_MINERVA", True)
    trigger_reason = _extract_bracket_field(payload["message"], "trigger")
    if (
        deep_dive_followup_enabled
        and routing.agent_id == "hermes"
        and payload["source"] == "telegram-inline-action"
        and trigger_reason == "telegram_inline_hermes_find_more"
    ):
        topic_key = _extract_bracket_field(payload["message"], "topic") or "unknown-topic"
        title = _extract_bracket_field(payload["message"], "title") or "Hermes Deep Dive"
        followup_lines = [
            "[trigger] hermes_deep_dive_auto_minerva_insight",
            f"[topic] {topic_key}",
            f"[title] {title}",
            "",
            "Hermes deep-dive 근거 수집이 완료되었습니다.",
            f"- deep_dive_vault_file: {shared_relative_vault_path}",
            f"- deep_dive_outbox_file: {outbox_path.name}",
            "- 요청: Minerva 2차적 사고 기반으로 인과/파급/리스크-기회/우선순위 액션 3개를 제시하세요.",
        ]
        followup_payload = {
            "agent_id": "minerva",
            "source": "agent-followup",
            "message": "\n".join(followup_lines),
            "triggered_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        followup_name = f"{now}-followup-minerva-{path.stem}.json"
        followup_path = inbox_dir / followup_name
        followup_path.write_text(json.dumps(followup_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        followup_minerva_inbox = followup_name

    if followup_minerva_inbox:
        outbox_payload["followup_minerva_inbox"] = followup_minerva_inbox
        outbox_path.write_text(json.dumps(outbox_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    archived_path = archive_dir / f"{now}-{path.name}"
    shutil.move(str(path), archived_path)


def process_pending_files(
    inbox_dir: Path,
    outbox_dir: Path,
    archive_dir: Path,
    vault_dir: Path,
    verified_inbox_dir: Path,
) -> None:
    for target in sorted(inbox_dir.iterdir()):
        if not target.is_file():
            continue
        if target.name.startswith("."):
            continue
        try:
            process_file(
                target,
                inbox_dir,
                outbox_dir,
                archive_dir,
                vault_dir,
                verified_inbox_dir,
            )
            print(f"[nanoclaw-agent] processed(poll): {target.name}")
        except FileNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"[nanoclaw-agent] failed(poll): {target.name} ({exc})")


class InboxHandler(FileSystemEventHandler):
    def __init__(
        self,
        inbox_dir: Path,
        outbox_dir: Path,
        archive_dir: Path,
        vault_dir: Path,
        verified_inbox_dir: Path,
    ) -> None:
        super().__init__()
        self.inbox_dir = inbox_dir
        self.outbox_dir = outbox_dir
        self.archive_dir = archive_dir
        self.vault_dir = vault_dir
        self.verified_inbox_dir = verified_inbox_dir

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        target = Path(event.src_path)
        if target.name.startswith("."):
            return

        time.sleep(0.2)
        try:
            process_file(
                target,
                self.inbox_dir,
                self.outbox_dir,
                self.archive_dir,
                self.vault_dir,
                self.verified_inbox_dir,
            )
            print(f"[nanoclaw-agent] processed: {target.name}")
        except Exception as exc:  # noqa: BLE001
            print(f"[nanoclaw-agent] failed: {target.name} ({exc})")


def main() -> None:
    root = Path(os.getenv("SHARED_ROOT", "/app/shared_data"))
    inbox_dir = root / os.getenv("INBOX_DIR", "inbox")
    outbox_dir = root / os.getenv("OUTBOX_DIR", "outbox")
    archive_dir = root / os.getenv("ARCHIVE_DIR", "archive")
    vault_dir = root / os.getenv("VAULT_DIR", "obsidian_vault")
    verified_inbox_dir = root / os.getenv("VERIFIED_INBOX_DIR", "verified_inbox")

    for directory in (inbox_dir, outbox_dir, archive_dir, vault_dir, verified_inbox_dir):
        ensure_dir(directory)

    event_handler = InboxHandler(inbox_dir, outbox_dir, archive_dir, vault_dir, verified_inbox_dir)
    observer = Observer()
    observer.schedule(event_handler, str(inbox_dir), recursive=False)
    observer.start()

    process_pending_files(inbox_dir, outbox_dir, archive_dir, vault_dir, verified_inbox_dir)
    print(f"[nanoclaw-agent] watching inbox: {inbox_dir}")

    try:
        while True:
            process_pending_files(inbox_dir, outbox_dir, archive_dir, vault_dir, verified_inbox_dir)
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()


if __name__ == "__main__":
    main()
