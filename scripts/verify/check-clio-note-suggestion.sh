#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env.local"
source "${REPO_ROOT}/scripts/runtime/load-env.sh"
load_runtime_env "$ENV_FILE"

get_env() {
  local key="$1"
  runtime_env_get "$key"
}

API_PORT="${API_PORT:-8001}"
WEBHOOK_SECRET="$(get_env TELEGRAM_WEBHOOK_SECRET)"
ALLOWED_USER_IDS="$(get_env TELEGRAM_ALLOWED_USER_IDS)"
ALLOWED_CHAT_IDS="$(get_env TELEGRAM_ALLOWED_CHAT_IDS)"

first_csv_value() {
  local raw="$1"
  raw="${raw%%,*}"
  raw="${raw//[[:space:]]/}"
  printf '%s' "$raw"
}

SOURCE_USER_ID="$(first_csv_value "$ALLOWED_USER_IDS")"
SOURCE_CHAT_ID="$(first_csv_value "$ALLOWED_CHAT_IDS")"
SOURCE_USER_ID="${SOURCE_USER_ID:-10001}"
SOURCE_CHAT_ID="${SOURCE_CHAT_ID:-$(get_env TELEGRAM_CHAT_ID)}"
SOURCE_CHAT_ID="${SOURCE_CHAT_ID:-20001}"

echo "[clio-suggestion] ensure llm-proxy runtime is up"
bash "${REPO_ROOT}/scripts/runtime/compose.sh" up -d llm-proxy >/dev/null
HEALTH_STATUS="000"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  HEALTH_STATUS="$(curl -sS -o /tmp/clio_suggestion_health.json -w '%{http_code}' "http://127.0.0.1:${API_PORT}/health" || true)"
  if [[ "$HEALTH_STATUS" == "200" ]]; then
    break
  fi
  sleep 1
done
if [[ "$HEALTH_STATUS" != "200" ]]; then
  echo "[clio-suggestion] llm-proxy healthcheck failed status=${HEALTH_STATUS}" >&2
  cat /tmp/clio_suggestion_health.json >&2 || true
  exit 1
fi

post_update_via_proxy() {
  local payload_file="$1"
  local output_file="$2"
  local secret="$3"
  local response
  response="$(
    docker exec -i nanoclaw-llm-proxy python -c 'import sys,urllib.request,urllib.error; secret=sys.argv[1]; body=sys.stdin.buffer.read(); headers={"content-type":"application/json"};
if secret: headers["x-telegram-bot-api-secret-token"]=secret
req=urllib.request.Request("http://127.0.0.1:8000/api/telegram/webhook", data=body, headers=headers, method="POST")
try:
 r=urllib.request.urlopen(req, timeout=10); raw=r.read(); status=r.status
except urllib.error.HTTPError as e:
 raw=e.read(); status=e.code
print(status); print(raw.decode("utf-8", errors="ignore"))' "$secret" < "$payload_file"
  )"
  printf '%s' "$response" | head -n 1
  printf '%s\n' "$response" | tail -n +2 >"$output_file"
}

post_message_text() {
  local message_text="$1"
  local output="$2"
  cat > /tmp/telegram_clio_suggestion_message.json <<JSON
{
  "update_id": 1,
  "message": {
    "message_id": 1,
    "from": { "id": ${SOURCE_USER_ID}, "username": "nanoclaw-clio-suggestion-test" },
    "chat": { "id": ${SOURCE_CHAT_ID}, "type": "private" },
    "text": "${message_text}"
  }
}
JSON
  local status
  status="$(post_update_via_proxy /tmp/telegram_clio_suggestion_message.json "$output" "$WEBHOOK_SECRET" || true)"
  if [[ "$status" != "200" ]]; then
    echo "[clio-suggestion] message webhook failed text=${message_text} status=${status}" >&2
    cat "$output" >&2 || true
    exit 1
  fi
}

post_callback_data() {
  local callback_data="$1"
  local output="$2"
  cat > /tmp/telegram_clio_suggestion_callback.json <<JSON
{
  "update_id": 1,
  "callback_query": {
    "id": "cbq-${RANDOM}",
    "data": "${callback_data}",
    "from": { "id": ${SOURCE_USER_ID}, "username": "nanoclaw-clio-suggestion-test" },
    "message": { "chat": { "id": ${SOURCE_CHAT_ID}, "type": "private" } }
  }
}
JSON
  local status
  status="$(post_update_via_proxy /tmp/telegram_clio_suggestion_callback.json "$output" "$WEBHOOK_SECRET" || true)"
  if [[ "$status" != "200" ]]; then
    echo "[clio-suggestion] callback failed data=${callback_data} status=${status}" >&2
    cat "$output" >&2 || true
    exit 1
  fi
}

RUN_ID="$(date +%Y%m%d-%H%M%S)-$RANDOM"
export CLIO_SUGGESTION_RUN_ID="$RUN_ID"
echo "[clio-suggestion] seed pending suggestion ${RUN_ID}"

SUGGESTION_ID="$(
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

run_id = os.environ["CLIO_SUGGESTION_RUN_ID"]
shared_root = Path("shared_data")
memory_path = shared_root / "shared_memory" / "clio_knowledge_memory.json"
payload = json.loads(memory_path.read_text(encoding="utf-8"))

draft_rel = f"obsidian_vault/01-Knowledge/Clio Suggestion Draft {run_id}.md"
target_rel = f"obsidian_vault/01-Knowledge/Clio Suggestion Target {run_id}.md"

draft_path = shared_root / draft_rel
target_path = shared_root / target_rel
draft_path.parent.mkdir(parents=True, exist_ok=True)
target_path.parent.mkdir(parents=True, exist_ok=True)

draft_path.write_text(
    "\n".join(
        [
            "---",
            f'title: "Clio Suggestion Draft {run_id}"',
            'type: "knowledge"',
            'draft_state: "draft"',
            'updated: "2026-03-08"',
            "---",
            "",
            "## 핵심 주장",
            "신규 초안입니다.",
        ]
    ),
    encoding="utf-8",
)

target_path.write_text(
    "\n".join(
        [
            "---",
            f'title: "Clio Suggestion Target {run_id}"',
            'type: "knowledge"',
            'draft_state: "confirmed"',
            'updated: "2026-03-08"',
            "---",
            "",
            "## 핵심 주장",
            "기존 지식 노트입니다.",
        ]
    ),
    encoding="utf-8",
)

recent = payload.setdefault("recentNotes", [])
recent.insert(
    0,
    {
        "title": f"Clio Suggestion Draft {run_id}",
        "type": "knowledge",
        "folder": "01-Knowledge",
        "templateName": "tpl-knowledge.md",
        "vaultFile": draft_rel,
        "tags": ["type/knowledge", "domain/pm"],
        "projectLinks": ["[[NanoClaw]]"],
        "mocCandidates": ["[[PM 스킬 맵]]"],
        "relatedNotes": [f"[[Clio Suggestion Target {run_id}]]"],
        "draftState": "draft",
        "claimReviewRequired": False,
        "claimReviewId": "",
        "noteAction": "update_candidate",
        "updateTarget": f"[[Clio Suggestion Target {run_id}]]",
        "updateTargetPath": target_rel,
        "mergeCandidates": [],
        "mergeCandidatePaths": [],
        "suggestionState": "pending",
        "updatedAt": "2026-03-08T00:00:00Z",
    },
)
memory_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(hashlib.sha256(draft_rel.encode("utf-8")).hexdigest()[:12])
PY
)"

if [[ -z "$SUGGESTION_ID" ]]; then
  echo "[clio-suggestion] suggestion id missing" >&2
  exit 1
fi

echo "[clio-suggestion] list pending suggestions"
post_message_text "/clio_suggestions" "/tmp/clio_suggestions_message.json"
python3 - <<'PY' "$SUGGESTION_ID"
import json, sys
payload = json.loads(open('/tmp/clio_suggestions_message.json','r',encoding='utf-8').read())
suggestion_id = sys.argv[1]
assert payload.get("ok") is True, payload
assert payload.get("command") == "/clio_suggestions", payload
assert int(payload.get("pendingCount", 0)) >= 1, payload
suggestion = payload.get("suggestion") or {}
assert str(suggestion.get("id") or "").strip() == suggestion_id, payload
diff_summary = suggestion.get("diffSummary") or []
assert isinstance(diff_summary, list) and diff_summary, payload
print("[clio-suggestion] pending suggestion listing ok")
PY

echo "[clio-suggestion] queue approval request"
post_callback_data "clio_apply_suggestion:${SUGGESTION_ID}" "/tmp/clio_suggestion_start.json"
APPROVAL_ID="$(
python3 - <<'PY'
import json
payload = json.loads(open('/tmp/clio_suggestion_start.json','r',encoding='utf-8').read())
assert payload.get("ok") is True, payload
assert payload.get("approvalRequired") is True, payload
approval = payload.get("approval") or {}
approval_id = str(approval.get("id") or "").strip()
assert approval_id, payload
print(approval_id)
PY
)"

echo "[clio-suggestion] early commit must fail"
post_callback_data "approval_commit:${APPROVAL_ID}" "/tmp/clio_suggestion_commit_early.json"
python3 - <<'PY'
import json
payload = json.loads(open('/tmp/clio_suggestion_commit_early.json','r',encoding='utf-8').read())
assert payload.get("reason") == "approval_not_pending_stage2", payload
print("[clio-suggestion] early commit rejected")
PY

echo "[clio-suggestion] approve stage1 + commit"
post_callback_data "approval_yes:${APPROVAL_ID}" "/tmp/clio_suggestion_yes.json"
post_callback_data "approval_commit:${APPROVAL_ID}" "/tmp/clio_suggestion_commit.json"
python3 - <<'PY' "$SUGGESTION_ID"
import json, sys
suggestion_id = sys.argv[1]
payload = json.loads(open('/tmp/clio_suggestion_commit.json','r',encoding='utf-8').read())
assert payload.get("ok") is True, payload
assert payload.get("action") == "clio_apply_suggestion", payload
suggestion = payload.get("approval") or {}
assert suggestion, payload
print("[clio-suggestion] commit response ok")
PY

python3 - <<'PY' "$RUN_ID" "$SUGGESTION_ID"
import json, sys
from pathlib import Path

run_id = sys.argv[1]
suggestion_id = sys.argv[2]
shared_root = Path("shared_data")
draft_rel = f"obsidian_vault/01-Knowledge/Clio Suggestion Draft {run_id}.md"
target_rel = f"obsidian_vault/01-Knowledge/Clio Suggestion Target {run_id}.md"

memory = json.loads((shared_root / "shared_memory" / "clio_knowledge_memory.json").read_text(encoding="utf-8"))
note = next(item for item in memory.get("recentNotes", []) if item.get("vaultFile") == draft_rel)
assert note.get("draftState") == "review", note
assert note.get("suggestionState") == "approved", note

target_body = (shared_root / target_rel).read_text(encoding="utf-8")
assert "## Clio Suggested Update" in target_body, target_body
assert f"<!-- clio-suggestion:{suggestion_id} -->" in target_body, target_body
draft_body = (shared_root / draft_rel).read_text(encoding="utf-8")
assert 'draft_state: "review"' in draft_body, draft_body
print("[clio-suggestion] memory + vault state ok")
PY

python3 - <<'PY' "$RUN_ID"
import json
import sys
from pathlib import Path

run_id = sys.argv[1]
shared_root = Path("shared_data")
memory_path = shared_root / "shared_memory" / "clio_knowledge_memory.json"
payload = json.loads(memory_path.read_text(encoding="utf-8"))
recent = payload.get("recentNotes", [])
payload["recentNotes"] = [
    item for item in recent
    if run_id not in str(item.get("title", "")) and run_id not in str(item.get("vaultFile", ""))
]
memory_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
for rel in (
    f"obsidian_vault/01-Knowledge/Clio Suggestion Draft {run_id}.md",
    f"obsidian_vault/01-Knowledge/Clio Suggestion Target {run_id}.md",
):
    path = shared_root / rel
    if path.exists():
        path.unlink()
print("[clio-suggestion] cleanup ok")
PY

echo "[clio-suggestion] PASS"
