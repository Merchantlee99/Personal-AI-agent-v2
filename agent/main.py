from __future__ import annotations

import json
import os
import re
import shutil
import time
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
    notebooklm_title: str
    notebooklm_summary: str


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

    first_line = next((line.strip() for line in message.splitlines() if line.strip()), "Clio Note")
    notebooklm_title = _truncate_text(first_line, 72)
    notebooklm_summary = _truncate_text(message, 240)

    return ClioPipelineResult(
        tags=tags,
        related_links=related_links,
        notebooklm_title=notebooklm_title,
        notebooklm_summary=notebooklm_summary,
    )


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
    lines = [
        f"# NanoClaw Inbox Capture ({agent_id})",
        "",
        f"- timestamp: {timestamp}",
        f"- source: {source}",
        f"- fallback_used: {str(fallback_used).lower()}",
        "",
        "## Message",
        message,
    ]
    if clio is not None:
        lines.extend(
            [
                "",
                "## Clio Metadata",
                f"- tags: {' '.join(clio.tags)}",
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
    if clio_pipeline is not None:
        verified_payload = {
            "agent_id": routing.agent_id,
            "source": payload["source"],
            "message": payload["message"],
            "tags": clio_pipeline.tags,
            "related_links": clio_pipeline.related_links,
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

    outbox_payload = {
        "agent_id": routing.agent_id,
        "fallback_used": routing.fallback_used,
        "input_file": path.name,
        "vault_file": shared_relative_vault_path,
        "vault_file_container": str(vault_path),
        "tags": clio_pipeline.tags if clio_pipeline else [],
        "related_links": clio_pipeline.related_links if clio_pipeline else [],
        "notebooklm_ready": clio_pipeline is not None,
        "verified_file": verified_relative_path,
        "processed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    outbox_path = outbox_dir / f"{now}-{path.stem}.json"
    outbox_path.write_text(json.dumps(outbox_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    archived_path = archive_dir / f"{now}-{path.name}"
    shutil.move(str(path), archived_path)


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

    print(f"[nanoclaw-agent] watching inbox: {inbox_dir}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()


if __name__ == "__main__":
    main()
