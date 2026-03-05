#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
if [[ -f "${REPO_ROOT}/.env.local" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${REPO_ROOT}/.env.local"
  set +a
fi

FRONTEND_PORT="${FRONTEND_PORT:-3000}"
BASE_URL="http://127.0.0.1:${FRONTEND_PORT}"
WEBHOOK_SECRET="${TELEGRAM_WEBHOOK_SECRET:-}"
ALLOWED_USER_IDS="${TELEGRAM_ALLOWED_USER_IDS:-}"
ALLOWED_CHAT_IDS="${TELEGRAM_ALLOWED_CHAT_IDS:-}"

first_csv_value() {
  local raw="$1"
  raw="${raw%%,*}"
  raw="${raw//[[:space:]]/}"
  printf '%s' "$raw"
}

SOURCE_USER_ID="$(first_csv_value "$ALLOWED_USER_IDS")"
SOURCE_CHAT_ID="$(first_csv_value "$ALLOWED_CHAT_IDS")"
SOURCE_USER_ID="${SOURCE_USER_ID:-10001}"
SOURCE_CHAT_ID="${SOURCE_CHAT_ID:-${TELEGRAM_CHAT_ID:-20001}}"

TOPIC_KEY="telegram-inline-rehearsal-$(date +%s)"

echo "[telegram-inline] create source event"
rm -f /tmp/telegram_inline_event.json
event_status="000"
for _ in 1 2 3 4 5; do
  event_status="$(
    curl -sS -o /tmp/telegram_inline_event.json -w '%{http_code}' \
      -X POST "${BASE_URL}/api/orchestration/events" \
      -H 'content-type: application/json' \
      -d "{
        \"schemaVersion\":1,
        \"agentId\":\"hermes\",
        \"topicKey\":\"${TOPIC_KEY}\",
        \"title\":\"텔레그램 인라인 버튼 리허설\",
        \"summary\":\"clio_save/hermes_deep_dive/minerva_insight 동작을 점검합니다.\",
        \"priority\":\"low\",
        \"confidence\":0.25,
        \"tags\":[\"rehearsal\",\"telegram\"],
        \"sourceRefs\":[{\"title\":\"Rehearsal Source\",\"url\":\"https://example.com/rehearsal\"}]
      }" || true
  )"
  if [[ "$event_status" == "200" ]]; then
    break
  fi
  sleep 1
done
if [[ "$event_status" != "200" ]]; then
  echo "[telegram-inline] failed to create source event status=$event_status" >&2
  cat /tmp/telegram_inline_event.json >&2 || true
  exit 1
fi

EVENT_ID="$(python3 - <<'PY'
import json
with open('/tmp/telegram_inline_event.json','r',encoding='utf-8') as f:
    data = json.load(f)
print(data.get("eventId",""))
PY
)"
if [[ -z "$EVENT_ID" ]]; then
  echo "[telegram-inline] eventId missing" >&2
  cat /tmp/telegram_inline_event.json >&2 || true
  exit 1
fi

post_callback_data() {
  local callback_data="$1"
  local output="$2"
  cat > /tmp/telegram_inline_callback.json <<JSON
{
  "update_id": 1,
  "callback_query": {
    "id": "cbq-${RANDOM}",
    "data": "${callback_data}",
    "from": { "id": ${SOURCE_USER_ID}, "username": "nanoclaw-inline-test" },
    "message": { "chat": { "id": ${SOURCE_CHAT_ID}, "type": "private" } }
  }
}
JSON

  local headers=(-H 'content-type: application/json')
  if [[ -n "$WEBHOOK_SECRET" ]]; then
    headers+=(-H "x-telegram-bot-api-secret-token: ${WEBHOOK_SECRET}")
  fi

  local status
  rm -f "$output"
  status="000"
  for _ in 1 2 3; do
    status="$(
      curl -sS -o "$output" -w '%{http_code}' \
        -X POST "${BASE_URL}/api/telegram/webhook" \
        "${headers[@]}" \
        -d @/tmp/telegram_inline_callback.json || true
    )"
    if [[ "$status" == "200" ]]; then
      break
    fi
    sleep 1
  done
  if [[ "$status" != "200" ]]; then
    echo "[telegram-inline] callback failed data=${callback_data} status=${status}" >&2
    cat "$output" >&2 || true
    exit 1
  fi
}

run_action_flow() {
  local action="$1"
  local output="/tmp/telegram_inline_${action}.json"
  post_callback_data "${action}:${EVENT_ID}" "$output"
  python3 - <<'PY' "$action" "$output"
import json,sys
from pathlib import Path

action = sys.argv[1]
output = Path(sys.argv[2])
payload = json.loads(output.read_text(encoding='utf-8'))

if not payload.get("ok"):
    raise SystemExit(f"[telegram-inline] {action} initial callback not ok: {payload}")

if payload.get("approvalRequired") is True:
    approval = payload.get("approval") or {}
    approval_id = str(approval.get("id") or "").strip()
    if not approval_id:
        raise SystemExit(f"[telegram-inline] {action} approval id missing: {payload}")
    print(f"[telegram-inline] {action} approval queued id={approval_id}")
    Path(f"/tmp/telegram_inline_{action}_approval_id.txt").write_text(approval_id, encoding='utf-8')
else:
    if payload.get("action") != action:
        raise SystemExit(f"[telegram-inline] {action} action mismatch: {payload}")
    inbox = payload.get("inbox") or {}
    if not isinstance(inbox, dict) or not inbox.get("inboxFile"):
        raise SystemExit(f"[telegram-inline] {action} inbox missing: {payload}")
    print(f"[telegram-inline] {action} immediate execution ok")
PY

  if [[ -f "/tmp/telegram_inline_${action}_approval_id.txt" ]]; then
    local approval_id
    approval_id="$(cat "/tmp/telegram_inline_${action}_approval_id.txt")"
    post_callback_data "approval_yes:${approval_id}" "/tmp/telegram_inline_${action}_yes.json"
    post_callback_data "approval_commit:${approval_id}" "/tmp/telegram_inline_${action}_commit.json"
    python3 - <<'PY' "$action" "/tmp/telegram_inline_${action}_commit.json"
import json,sys
action = sys.argv[1]
path = sys.argv[2]
payload = json.loads(open(path,'r',encoding='utf-8').read())
if not payload.get("ok") or payload.get("action") != action:
    raise SystemExit(f"[telegram-inline] {action} commit failed: {payload}")
inbox = payload.get("inbox") or {}
if not isinstance(inbox, dict) or not inbox.get("inboxFile"):
    raise SystemExit(f"[telegram-inline] {action} commit inbox missing: {payload}")
print(f"[telegram-inline] {action} approval execution ok")
PY
    rm -f "/tmp/telegram_inline_${action}_approval_id.txt"
  fi
}

echo "[telegram-inline] callback clio_save"
run_action_flow "clio_save"

echo "[telegram-inline] callback hermes_deep_dive"
run_action_flow "hermes_deep_dive"

echo "[telegram-inline] callback minerva_insight"
run_action_flow "minerva_insight"

echo "[telegram-inline] PASS"
