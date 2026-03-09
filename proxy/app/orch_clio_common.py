from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable


def vault_root(root: Path) -> Path:
    return (root / "obsidian_vault").resolve()


def safe_vault_path(root: Path, relative_or_absolute: str, sanitize_text: Callable[[Any, int], str]) -> Path | None:
    raw = sanitize_text(relative_or_absolute, 260)
    if not raw:
        return None
    candidate = Path(raw)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(vault_root(root))
    except ValueError:
        return None
    return resolved


def update_frontmatter_scalar(markdown: str, key: str, value: str) -> str:
    match = re.match(r"(?s)\A---\n(.*?)\n---\n?", markdown)
    if not match:
        return markdown
    lines = match.group(1).splitlines()
    replacement = f'{key}: {json.dumps(str(value), ensure_ascii=False)}'
    next_lines: list[str] = []
    found = False
    for line in lines:
        if re.match(rf"^{re.escape(key)}:\s*", line):
            next_lines.append(replacement)
            found = True
            continue
        next_lines.append(line)
    if not found:
        next_lines.append(replacement)
    frontmatter = "---\n" + "\n".join(next_lines) + "\n---\n"
    return frontmatter + markdown[match.end() :]


def strip_frontmatter(markdown: str) -> str:
    if not markdown.startswith("---"):
        return markdown
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return markdown
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[index + 1 :]).strip()
    return markdown


def extract_diff_candidate_lines(markdown: str, *, sanitize_text: Callable[[Any, int], str], limit: int = 3) -> list[str]:
    body = strip_frontmatter(markdown)
    lines: list[str] = []
    for raw in body.splitlines():
        line = sanitize_text(raw, 180)
        if not line:
            continue
        if line.startswith("## "):
            line = line[3:].strip()
        elif line.startswith("### "):
            line = line[4:].strip()
        if len(line) < 8:
            continue
        lines.append(line)
        if len(lines) >= max(1, limit * 3):
            break
    return lines


def append_note_annotation(note_path: Path, marker: str, heading: str, lines: list[str]) -> bool:
    if not note_path.is_file():
        return False
    markdown = note_path.read_text(encoding="utf-8")
    if marker in markdown:
        return True
    block = "\n".join(["", heading, marker, *lines]).rstrip() + "\n"
    note_path.write_text(markdown.rstrip() + "\n" + block, encoding="utf-8")
    return True
