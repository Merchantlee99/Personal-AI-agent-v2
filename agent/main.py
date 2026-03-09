from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clio_pipeline import (
    CLIO_OBSIDIAN_FORMAT_VERSION,
    ClioPipelineResult,
    _dedupe_preserve_order,
    _extract_bracket_field,
    _next_available_note_path,
    _sanitize_file_stem,
    _slugify,
    build_markdown,
    dispatch_notebooklm_sync,
    infer_clio_pipeline,
)
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


@dataclass
class RoutingResult:
    agent_id: str
    fallback_used: bool

RUNTIME_NOTES_DIRNAME = "runtime_agent_notes"
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
    raise ValueError(f"unknown agent_id: {raw.strip() or '<empty>'}")


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
        clio_pipeline = infer_clio_pipeline(
            payload["message"],
            vault_dir,
            payload["source"],
            tag_taxonomy=TAG_TAXONOMY,
            project_registry=PROJECT_REGISTRY,
            moc_registry=MOC_REGISTRY,
            canonical_ids=CANONICAL_IDS,
        )

    markdown = build_markdown(
        agent_id=routing.agent_id,
        source=payload["source"],
        message=payload["message"],
        fallback_used=routing.fallback_used,
        clio=clio_pipeline,
    )
    runtime_notes_dir = vault_dir.parent / RUNTIME_NOTES_DIRNAME
    vault_target_dir = (
        vault_dir / clio_pipeline.folder
        if clio_pipeline is not None
        else runtime_notes_dir / datetime.now(UTC).strftime("%Y-%m-%d")
    )
    ensure_dir(vault_target_dir)
    file_stem = _sanitize_file_stem(clio_pipeline.title if clio_pipeline is not None else routing.agent_id)
    if clio_pipeline is not None:
        vault_path = _next_available_note_path(vault_target_dir, file_stem)
    else:
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


def quarantine_file(path: Path, archive_dir: Path, reason: str) -> None:
    if not path.exists() or not path.is_file():
        return

    now = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    quarantine_dir = archive_dir / "quarantine" / datetime.now(UTC).strftime("%Y-%m-%d")
    ensure_dir(quarantine_dir)

    target = quarantine_dir / f"{now}-{path.name}"
    shutil.move(str(path), target)

    error_sidecar = target.with_suffix(target.suffix + ".error.json")
    error_sidecar.write_text(
        json.dumps(
            {
                "reason": reason,
                "quarantined_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "file": target.name,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


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
        except (ValueError, json.JSONDecodeError) as exc:
            quarantine_file(target, archive_dir, str(exc))
            print(f"[nanoclaw-agent] quarantined(poll): {target.name} ({exc})")
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
        except (ValueError, json.JSONDecodeError) as exc:
            quarantine_file(target, self.archive_dir, str(exc))
            print(f"[nanoclaw-agent] quarantined: {target.name} ({exc})")
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
