from __future__ import annotations

import os
import re
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clio_core import (
    CLIO_DRAFT_STATE_DEFAULT,
    CLIO_OBSIDIAN_FORMAT_VERSION,
    CLIO_STATUS_DEFAULT,
    NOTE_TYPE_DEFAULT,
    TEMPLATE_FILE_BY_TYPE,
    URL_PATTERN,
    ClioPipelineResult,
    _dedupe_preserve_order,
    _extract_bracket_field,
    _extract_source_lines,
    _extract_tokens,
    _is_user_facing_note,
    _meaningful_lines,
    _next_available_note_path,
    _sanitize_file_stem,
    _slugify,
    _strip_inline_bracket_fields,
    _truncate_text,
)
from clio_notebooklm import (
    _normalize_language,
    detect_source_language,
    dispatch_notebooklm_sync,
    parse_bool_env,
    translate_with_deepl,
)
from clio_render import build_markdown


def _claim_like_title(text: str) -> str:
    cleaned = _strip_inline_bracket_fields(text)
    cleaned = re.sub(r'^["\'“”‘’]+|["\'“”‘’]+$', "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:;,.")
    if not cleaned:
        return "Clio Draft"
    if len(cleaned) > 96:
        sentence = re.split(r"(?<=[.!?다요])\s+", cleaned)[0].strip()
        if sentence:
            cleaned = sentence
    cleaned = cleaned.rstrip(".!? ")
    return _truncate_text(cleaned, 96)


def _derive_title(message: str, note_type: str) -> str:
    bracket_title = _extract_bracket_field(message, "title")
    if bracket_title:
        return _truncate_text(bracket_title, 96)
    for line in _meaningful_lines(message):
        compact = re.sub(r"^(다음 내용을|요청:)\s*", "", line).strip(" -:")
        if compact:
            if note_type == "knowledge":
                return _claim_like_title(compact)
            return _truncate_text(compact, 96)
    return "Clio Draft"


def _derive_summary(message: str) -> str:
    for line in _meaningful_lines(message):
        if len(line) >= 8:
            cleaned = re.sub(r"\s+", " ", line).strip(" -:")
            return _truncate_text(cleaned, 220)
    return _truncate_text(" ".join(_extract_tokens(message)), 220)


def _extract_source_urls(message: str) -> list[str]:
    urls = URL_PATTERN.findall(message)
    return _dedupe_preserve_order(urls)[:8]


def _source_tag_from_url(url: str, tag_taxonomy: dict[str, Any]) -> str | None:
    try:
        host = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return None
    for entry in tag_taxonomy.get("sources", []):
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


def _infer_domain_tags(message: str, note_type: str, tag_taxonomy: dict[str, Any]) -> list[str]:
    lowered = message.lower()
    tags: list[str] = []
    for entry in tag_taxonomy.get("domains", []):
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


def _match_projects(message: str, project_registry: dict[str, Any], canonical_ids: set[str]) -> list[dict[str, str]]:
    lowered = message.lower()
    matches: list[dict[str, str]] = []
    for entry in project_registry.get("projects", []):
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
            if not token or token in canonical_ids:
                continue
            sanitized_aliases.append(token)
        if any(token in lowered for token in sanitized_aliases):
            matches.append({"name": name, "slug": slug, "folder": folder, "moc": moc})
    return matches[:3]


def _infer_moc_candidates(
    note_type: str,
    domain_tags: list[str],
    projects: list[dict[str, str]],
    moc_registry: dict[str, Any],
) -> list[str]:
    project_slugs = {item.get("slug", "") for item in projects}
    candidates: list[str] = [item.get("moc", "") for item in projects if item.get("moc")]
    for entry in moc_registry.get("mocs", []):
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
        if not _is_user_facing_note(note_path, vault_dir):
            continue
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
        if not _is_user_facing_note(note_path, vault_dir):
            continue
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


def _route_folder(note_type: str, projects: list[dict[str, str]], message: str) -> str:
    explicit_folder = _extract_bracket_field(message, "folder")
    if explicit_folder:
        return explicit_folder.strip().rstrip("/")
    project_note = _extract_bracket_field(message, "project_note").lower()
    if project_note in {"true", "yes", "1"} and projects and projects[0].get("folder"):
        return str(projects[0]["folder"]).strip().rstrip("/")
    return {
        "study": "01-Knowledge",
        "article": "02-References",
        "paper": "02-References",
        "knowledge": "01-Knowledge",
        "writing": "04-Writing",
        "skill": "01-Knowledge",
    }.get(note_type, "00-Inbox")


def infer_clio_pipeline(
    message: str,
    vault_dir: Path,
    source: str,
    *,
    tag_taxonomy: dict[str, Any],
    project_registry: dict[str, Any],
    moc_registry: dict[str, Any],
    canonical_ids: set[str],
) -> ClioPipelineResult:
    source_urls = _extract_source_urls(message)
    note_type, classification_confidence = _infer_note_type(message, source_urls, source)
    title = _derive_title(message, note_type)
    summary = _derive_summary(message)
    projects = _match_projects(message, project_registry, canonical_ids)
    folder = _route_folder(note_type, projects, message)
    template_name = TEMPLATE_FILE_BY_TYPE.get(note_type, TEMPLATE_FILE_BY_TYPE[NOTE_TYPE_DEFAULT])
    domain_tags = _infer_domain_tags(message, note_type, tag_taxonomy)
    project_links = [f"[[{item['name']}]]" for item in projects if item.get("name")]
    moc_candidates = _infer_moc_candidates(note_type, domain_tags, projects, moc_registry)
    source_tags = [tag for tag in (_source_tag_from_url(url, tag_taxonomy) for url in source_urls[:2]) if tag]
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
