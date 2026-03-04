from __future__ import annotations

import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


@dataclass
class RoutingResult:
    agent_id: str
    fallback_used: bool


@dataclass
class ClioPipelineResult:
    tags: list[str]
    related_links: list[str]
    source_urls: list[str]
    notebooklm_title: str
    notebooklm_summary: str
    source_language: str
    deepl_target_lang: str
    deepl_required: bool
    deepl_applied: bool


TAG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("#clio", ("clio",)),
    ("#knowledge", ("knowledge", "문서", "지식", "정리")),
    ("#notebooklm", ("notebooklm", "notebook lm")),
    ("#ai", ("ai", "인공지능", "llm", "모델")),
    ("#agent", ("agent", "agentic", "에이전트")),
    ("#trend", ("trend", "trends", "동향", "트렌드")),
    ("#research", ("research", "report", "analysis", "리포트", "분석")),
)

LINK_RULES: tuple[tuple[str, str], ...] = (
    ("minerva", "Minerva-Orchestration-Notes"),
    ("hermes", "Hermes-Daily-Briefing"),
    ("clio", "Clio-Knowledge-Base"),
    ("notebooklm", "NotebookLM-Staging"),
    ("trend", "Market-Trend-Tracker"),
    ("report", "Research-Report-Index"),
)

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


CANONICAL_IDS = load_agent_ids()


def normalize_agent_id(raw: str) -> RoutingResult:
    token = raw.strip().lower()
    if token in CANONICAL_IDS:
        return RoutingResult(agent_id=token, fallback_used=False)
    return RoutingResult(agent_id="minerva", fallback_used=True)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


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


def _yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _extract_bracket_field(message: str, field_name: str) -> str:
    prefix = f"[{field_name}]"
    for line in message.splitlines():
        token = line.strip()
        if not token.lower().startswith(prefix.lower()):
            continue
        return token[len(prefix) :].strip()
    return ""


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


def infer_clio_pipeline(message: str, vault_dir: Path) -> ClioPipelineResult:
    lowered = message.lower()

    tags = ["#clio", "#knowledge-pipeline"]
    for tag, keywords in TAG_RULES:
        if any(keyword in lowered for keyword in keywords):
            tags.append(tag)

    dynamic_tags: list[str] = []
    for token in _extract_tokens(message):
        if re.fullmatch(r"[a-z0-9-]{4,20}", token):
            dynamic_tags.append(f"#{token}")
        if len(dynamic_tags) >= 3:
            break
    tags.extend(dynamic_tags)
    tags = _dedupe_preserve_order(tags)

    related_links: list[str] = []
    for keyword, note_name in LINK_RULES:
        if keyword in lowered:
            related_links.append(f"[[{note_name}]]")

    # Keep linking deterministic and lightweight by matching recent note names only.
    recent_note_links: list[str] = []
    for note_path in sorted(vault_dir.rglob("*.md"), reverse=True)[:40]:
        stem = note_path.stem
        if stem.startswith("index"):
            continue
        if re.match(r"^\d{8}-\d{6}-", stem):
            continue
        if any(token in stem.lower() for token in _extract_tokens(message)):
            recent_note_links.append(f"[[{stem}]]")
        if len(recent_note_links) >= 3:
            break
    related_links.extend(recent_note_links)

    external_urls = _dedupe_preserve_order(URL_PATTERN.findall(message))
    related_links.extend(external_urls[:3])
    related_links = _dedupe_preserve_order(related_links)[:8]
    source_urls = external_urls[:8]

    first_line = next((line.strip() for line in message.splitlines() if line.strip()), "Clio Note")
    notebooklm_title = _truncate_text(first_line, 72)
    source_language = detect_source_language(message)
    deepl_target_lang = re.sub(r"[^A-Za-z]", "", os.getenv("DEEPL_TARGET_LANG", "KO")).upper() or "KO"
    source_token = _normalize_language(source_language).upper()
    deepl_required = source_token not in {"", "UNKNOWN"} and source_token != deepl_target_lang

    notebooklm_summary = _truncate_text(message, 240)
    deepl_applied = False
    if deepl_required:
        translated_summary = translate_with_deepl(notebooklm_summary, source_language, deepl_target_lang)
        if translated_summary:
            notebooklm_summary = _truncate_text(translated_summary, 240)
            deepl_applied = True

    return ClioPipelineResult(
        tags=tags,
        related_links=related_links,
        source_urls=source_urls,
        notebooklm_title=notebooklm_title,
        notebooklm_summary=notebooklm_summary,
        source_language=source_language,
        deepl_target_lang=deepl_target_lang,
        deepl_required=deepl_required,
        deepl_applied=deepl_applied,
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
    lines = ["---", f"agent_id: {_yaml_quote(agent_id)}", f"timestamp: {_yaml_quote(timestamp)}", f"source: {_yaml_quote(source)}", f"fallback_used: {str(fallback_used).lower()}"]
    if clio is not None:
        lines.append("tags:")
        for tag in clio.tags:
            lines.append(f"  - {_yaml_quote(tag)}")
        lines.append("source_urls:")
        if clio.source_urls:
            for url in clio.source_urls:
                lines.append(f"  - {_yaml_quote(url)}")
        else:
            lines.append("  - \"\"")
        lines.extend(
            [
                f"source_language: {_yaml_quote(clio.source_language)}",
                "deepl:",
                f"  target_lang: {_yaml_quote(clio.deepl_target_lang)}",
                f"  required: {str(clio.deepl_required).lower()}",
                f"  applied: {str(clio.deepl_applied).lower()}",
                "notebooklm:",
                "  ready: true",
                f"  title: {_yaml_quote(clio.notebooklm_title)}",
            ]
        )
    lines.extend(["---", "", f"# NanoClaw Inbox Capture ({agent_id})", "", "## Message", message])
    if clio is not None:
        lines.extend(
            [
                "",
                "## Clio Metadata",
                f"- tags: {' '.join(clio.tags)}",
                f"- source_language: {clio.source_language}",
                f"- source_urls: {', '.join(clio.source_urls) if clio.source_urls else '없음'}",
                f"- deepl_required: {str(clio.deepl_required).lower()}",
                f"- deepl_applied: {str(clio.deepl_applied).lower()}",
                f"- notebooklm_title: {clio.notebooklm_title}",
                f"- notebooklm_ready: true",
                "",
                "## Clio Links",
            ]
        )
        if clio.related_links:
            lines.extend([f"- {item}" for item in clio.related_links])
        else:
            lines.append("- 없음")
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

    vault_daily = vault_dir / datetime.now(UTC).strftime("%Y-%m-%d")
    ensure_dir(vault_daily)

    clio_pipeline: ClioPipelineResult | None = None
    if routing.agent_id == "clio":
        clio_pipeline = infer_clio_pipeline(payload["message"], vault_dir)

    markdown = build_markdown(
        agent_id=routing.agent_id,
        source=payload["source"],
        message=payload["message"],
        fallback_used=routing.fallback_used,
        clio=clio_pipeline,
    )
    vault_path = vault_daily / f"{now}-{routing.agent_id}.md"
    vault_path.write_text(markdown, encoding="utf-8")
    shared_relative_vault_path = vault_path.relative_to(vault_dir.parent).as_posix()

    verified_relative_path: str | None = None
    notebooklm_dispatch: dict[str, object] = {"attempted": False, "delivered": False, "reason": "not_clio"}
    if clio_pipeline is not None:
        verified_payload = {
            "agent_id": routing.agent_id,
            "source": payload["source"],
            "message": payload["message"],
            "tags": clio_pipeline.tags,
            "related_links": clio_pipeline.related_links,
            "source_urls": clio_pipeline.source_urls,
            "source_language": clio_pipeline.source_language,
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
        "fallback_used": routing.fallback_used,
        "input_file": path.name,
        "vault_file": shared_relative_vault_path,
        "vault_file_container": str(vault_path),
        "tags": clio_pipeline.tags if clio_pipeline else [],
        "related_links": clio_pipeline.related_links if clio_pipeline else [],
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
