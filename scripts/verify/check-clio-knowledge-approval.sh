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
BASE_URL="http://127.0.0.1:${API_PORT}"
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
  cat > /tmp/telegram_clio_review_message.json <<JSON
{
  "update_id": 1,
  "message": {
    "message_id": 1,
    "from": { "id": ${SOURCE_USER_ID}, "username": "nanoclaw-clio-review-test" },
    "chat": { "id": ${SOURCE_CHAT_ID}, "type": "private" },
    "text": "${message_text}"
  }
}
JSON
  local status
  status="$(post_update_via_proxy /tmp/telegram_clio_review_message.json "$output" "$WEBHOOK_SECRET" || true)"
  if [[ "$status" != "200" ]]; then
    echo "[clio-approval] message webhook failed text=${message_text} status=${status}" >&2
    cat "$output" >&2 || true
    exit 1
  fi
}

post_callback_data() {
  local callback_data="$1"
  local output="$2"
  cat > /tmp/telegram_clio_review_callback.json <<JSON
{
  "update_id": 1,
  "callback_query": {
    "id": "cbq-${RANDOM}",
    "data": "${callback_data}",
    "from": { "id": ${SOURCE_USER_ID}, "username": "nanoclaw-clio-review-test" },
    "message": { "chat": { "id": ${SOURCE_CHAT_ID}, "type": "private" } }
  }
}
JSON
  local status
  status="$(post_update_via_proxy /tmp/telegram_clio_review_callback.json "$output" "$WEBHOOK_SECRET" || true)"
  if [[ "$status" != "200" ]]; then
    echo "[clio-approval] callback failed data=${callback_data} status=${status}" >&2
    cat "$output" >&2 || true
    exit 1
  fi
}

echo "[clio-approval] create pending knowledge review"
bash "${REPO_ROOT}/scripts/verify/check-clio-knowledge-review.sh" >/tmp/clio_knowledge_seed.log

REVIEW_ID="$(
python3 - <<'PY'
import json
from pathlib import Path

path = Path("shared_data/shared_memory/clio_claim_review_queue.json")
payload = json.loads(path.read_text(encoding="utf-8"))
items = [item for item in payload.get("items", []) if item.get("status") == "pending_user_review"]
items.sort(key=lambda row: str(row.get("requestedAt", "")), reverse=True)
print(items[0]["id"] if items else "")
PY
)"
if [[ -z "$REVIEW_ID" ]]; then
  echo "[clio-approval] review id missing" >&2
  exit 1
fi

echo "[clio-approval] list pending reviews"
post_message_text "/clio_reviews" "/tmp/clio_reviews_message.json"
python3 - <<'PY' "$REVIEW_ID"
import json, sys
payload = json.loads(open('/tmp/clio_reviews_message.json','r',encoding='utf-8').read())
review_id = sys.argv[1]
assert payload.get("ok") is True, f"message response not ok: {payload}"
assert payload.get("command") == "/clio_reviews", payload
assert int(payload.get("pendingCount", 0)) >= 1, payload
review = payload.get("review") or {}
assert str(review.get("id") or "").strip() == review_id, payload
print("[clio-approval] pending review listing ok")
PY

echo "[clio-approval] queue approval request"
post_callback_data "clio_confirm_knowledge:${REVIEW_ID}" "/tmp/clio_review_start.json"
APPROVAL_ID="$(
python3 - <<'PY'
import json
payload = json.loads(open('/tmp/clio_review_start.json','r',encoding='utf-8').read())
assert payload.get("ok") is True, payload
assert payload.get("approvalRequired") is True, payload
approval = payload.get("approval") or {}
approval_id = str(approval.get("id") or "").strip()
assert approval_id, payload
print(approval_id)
PY
)"

echo "[clio-approval] early commit must fail"
post_callback_data "approval_commit:${APPROVAL_ID}" "/tmp/clio_review_commit_early.json"
python3 - <<'PY'
import json
payload = json.loads(open('/tmp/clio_review_commit_early.json','r',encoding='utf-8').read())
assert payload.get("reason") == "approval_not_pending_stage2", payload
print("[clio-approval] early commit rejected")
PY

echo "[clio-approval] approve stage1 + commit"
post_callback_data "approval_yes:${APPROVAL_ID}" "/tmp/clio_review_yes.json"
post_callback_data "approval_commit:${APPROVAL_ID}" "/tmp/clio_review_commit.json"
python3 - <<'PY' "$REVIEW_ID"
import json, sys
review_id = sys.argv[1]
payload = json.loads(open('/tmp/clio_review_commit.json','r',encoding='utf-8').read())
assert payload.get("ok") is True, payload
assert payload.get("action") == "clio_confirm_knowledge", payload
claim_review = payload.get("claimReview") or {}
assert str(claim_review.get("id") or "") == review_id, payload
assert claim_review.get("status") == "confirmed_by_user", payload
print("[clio-approval] commit response ok")
PY

python3 - <<'PY' "$REVIEW_ID"
import json, sys
from pathlib import Path

review_id = sys.argv[1]
queue = json.loads(Path("shared_data/shared_memory/clio_claim_review_queue.json").read_text(encoding="utf-8"))
item = next(item for item in queue.get("items", []) if item.get("id") == review_id)
assert item.get("status") == "confirmed_by_user", item
vault_file = str(item.get("vaultFile") or "")
memory = json.loads(Path("shared_data/shared_memory/clio_knowledge_memory.json").read_text(encoding="utf-8"))
note = next(note for note in memory.get("recentNotes", []) if note.get("claimReviewId") == review_id)
assert note.get("draftState") == "confirmed", note
assert note.get("claimReviewRequired") is False, note
note_path = Path("shared_data") / vault_file
body = note_path.read_text(encoding="utf-8")
assert 'draft_state: \"confirmed\"' in body, body
print("[clio-approval] queue, memory, vault note state ok")
PY

echo "[clio-approval] PASS"
