#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

SHARED_ROOT="${SHARED_ROOT_PATH:-${ROOT_DIR}/shared_data}"
VERIFIED_DIR="${SHARED_ROOT}/verified_inbox"
FORMAT_VERSION_EXPECTED="clio_obsidian_v2"

echo "[clio-format] scanning latest clio verified payload"

if [[ ! -d "$VERIFIED_DIR" ]]; then
  echo "[clio-format] verified_inbox directory missing: $VERIFIED_DIR" >&2
  exit 1
fi

latest_file="$(
  python3 - <<'PY' "$VERIFIED_DIR"
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
candidates: list[pathlib.Path] = []

for path in root.rglob("*.json"):
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        if payload.get("agent_id") == "clio":
            candidates.append(path)
    except Exception:
        continue

if not candidates:
    print("")
else:
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    print(candidates[0])
PY
)"

if [[ -z "${latest_file:-}" ]]; then
  echo "[clio-format] no clio verified payload found under $VERIFIED_DIR" >&2
  exit 1
fi

echo "[clio-format] latest_file=$latest_file"

python3 - <<'PY' "$latest_file" "$FORMAT_VERSION_EXPECTED" "$SHARED_ROOT"
import json
import pathlib
import sys

payload_path = pathlib.Path(sys.argv[1])
expected_version = sys.argv[2]
shared_root = pathlib.Path(sys.argv[3])

with payload_path.open("r", encoding="utf-8") as f:
    payload = json.load(f)

assert payload.get("agent_id") == "clio", f"agent_id is not clio: {payload.get('agent_id')}"
assert payload.get("format_version") == expected_version, (
    f"format_version mismatch: {payload.get('format_version')} != {expected_version}"
)
assert isinstance(payload.get("type"), str) and payload["type"], "type missing"
assert isinstance(payload.get("folder"), str) and payload["folder"], "folder missing"
assert isinstance(payload.get("template_name"), str) and payload["template_name"].startswith("tpl-"), "template_name missing"
assert payload.get("draft_state") == "draft", f"draft_state mismatch: {payload.get('draft_state')}"
assert isinstance(payload.get("classification_confidence"), (int, float)), "classification_confidence missing"
assert isinstance(payload.get("project_links"), list), "project_links missing"
assert isinstance(payload.get("moc_candidates"), list), "moc_candidates missing"
assert isinstance(payload.get("related_notes"), list), "related_notes missing"
assert isinstance(payload.get("frontmatter"), dict), "frontmatter missing"

notebooklm = payload.get("notebooklm") or {}
assert notebooklm.get("ready") is True, "notebooklm.ready must be true for clio verified payload"
vault_file = notebooklm.get("vault_file")
assert isinstance(vault_file, str) and vault_file.strip(), "notebooklm.vault_file missing"

vault_path = shared_root / vault_file
assert vault_path.is_file(), f"vault markdown not found: {vault_path}"
content = vault_path.read_text(encoding="utf-8")
assert "clio_format_version: \"clio_obsidian_v2\"" in content, "frontmatter clio_format_version missing"
assert "## Clio Metadata" in content, "Clio metadata section missing"
assert "## Clio Relationships" in content, "Clio relationships section missing"
assert "## NotebookLM Summary" in content, "NotebookLM summary section missing"
assert "# NanoClaw Inbox Capture" not in content, "legacy inbox capture header still present"

print("[clio-format] payload + vault contract verified")
print(f"[clio-format] vault_file={vault_file}")
PY

echo "[clio-format] PASS"
