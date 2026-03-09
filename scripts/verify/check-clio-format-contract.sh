#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
source scripts/runtime/compose-env.sh
source scripts/runtime/load-env.sh

ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env.local}"
load_runtime_env "$ENV_FILE"

FORMAT_VERSION_EXPECTED="clio_obsidian_v2"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$RANDOM"
INPUT_BASENAME="clio-format-${RUN_ID}"
INPUT_FILE="shared_data/inbox/${INPUT_BASENAME}.json"

cleanup() {
  rm -f "$INPUT_FILE"
  find shared_data/outbox -type f -name "*-${INPUT_BASENAME}.json" -delete 2>/dev/null || true
  find shared_data/verified_inbox -type f -name "*-${INPUT_BASENAME}.json" -delete 2>/dev/null || true
  find shared_data/archive -type f -name "*-${INPUT_BASENAME}.json" -delete 2>/dev/null || true
  if [[ -n "${VAULT_PATH_TO_DELETE:-}" && -f "${VAULT_PATH_TO_DELETE}" ]]; then
    rm -f "${VAULT_PATH_TO_DELETE}"
  fi
}
trap cleanup EXIT

echo "[clio-format] ensure nanoclaw-agent is running"
compose_cmd up -d nanoclaw-agent >/dev/null

cat >"$INPUT_FILE" <<JSON
{"agent_id":"clio","source":"format-contract","message":"[title] AI PM은 실험 로그를 반복 가능한 지식으로 바꿔야 한다\n[topic] clio-format-${RUN_ID}\n[domain] pm\n\n프로덕트 실험 회고를 재사용 가능한 지식으로 바꾸는 방법을 정리한다. https://example.com/clio-format-${RUN_ID}"}
JSON

echo "[clio-format] wait for verified payload"
VERIFIED_PATH=""
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  VERIFIED_PATH="$(find shared_data/verified_inbox -type f -name "*-${INPUT_BASENAME}.json" | sort | tail -n 1)"
  if [[ -n "$VERIFIED_PATH" ]]; then
    break
  fi
  sleep 1
done

if [[ -z "$VERIFIED_PATH" ]]; then
  echo "[clio-format] verified payload not found for ${INPUT_BASENAME}" >&2
  exit 1
fi

echo "[clio-format] verified_file=${VERIFIED_PATH}"

VALIDATION_OUTPUT="$(
python3 - <<'PY' "$VERIFIED_PATH" "$FORMAT_VERSION_EXPECTED"
import json
import pathlib
import sys

payload_path = pathlib.Path(sys.argv[1])
expected_version = sys.argv[2]
shared_root = pathlib.Path("shared_data")

payload = json.loads(payload_path.read_text(encoding="utf-8"))

assert payload.get("agent_id") == "clio", f"agent_id is not clio: {payload.get('agent_id')}"
assert payload.get("source") == "format-contract", f"unexpected source: {payload.get('source')}"
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
assert 'clio_format_version: "clio_obsidian_v2"' in content, "frontmatter clio_format_version missing"
assert "# NanoClaw Inbox Capture" not in content, "legacy inbox capture header still present"
assert "## Clio Metadata" not in content, "user-facing note still contains Clio metadata section"
assert "## Clio Relationships" not in content, "user-facing note still contains Clio relationships section"
assert "## NotebookLM Summary" not in content, "user-facing note still contains NotebookLM summary section"
assert "## Routing Rules" not in content, "user-facing note still contains routing rules section"

print(vault_path)
PY
)"

VAULT_PATH_TO_DELETE="$(printf '%s\n' "$VALIDATION_OUTPUT" | tail -n 1)"
echo "[clio-format] payload + vault contract verified"
echo "[clio-format] vault_file=${VAULT_PATH_TO_DELETE#${ROOT_DIR}/shared_data/}"
echo "[clio-format] PASS"
