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
OCCURRED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "[telegram-inline] create source event"
rm -f /tmp/telegram_inline_event.json
event_status="000"
for _ in 1 2 3 4 5; do
  event_status="$(
    curl -sS -o /tmp/telegram_inline_event.json -w '%{http_code}' \
      -X POST "${BASE_URL}/api/orchestration/events" \
      -H 'content-type: application/json' \
      -d "{
        \"schemaVersion\":\"1.0\",
        \"eventType\":\"hermes.briefing.created\",
        \"producer\":\"verify-script\",
        \"occurredAt\":\"${OCCURRED_AT}\",
        \"payload\":{
          \"agentId\":\"hermes\",
          \"topicKey\":\"${TOPIC_KEY}\",
          \"title\":\"텔레그램 인라인 버튼 리허설\",
          \"summary\":\"clio_save/hermes_deep_dive/minerva_insight 동작을 점검합니다.\",
          \"priority\":\"low\",
          \"confidence\":0.25,
          \"tags\":[\"rehearsal\",\"telegram\"],
          \"sourceRefs\":[{\"title\":\"Rehearsal Source\",\"url\":\"https://example.com/rehearsal\"}]
        }
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

post_callback() {
  local callback_data="$1"
  local callback_id="$2"
  local output="$3"
  cat > /tmp/telegram_inline_callback.json <<JSON
{
  "update_id": 1,
  "callback_query": {
    "id": "${callback_id}",
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

extract_approval_id() {
  local path="$1"
  python3 - <<'PY' "$path"
import json,sys
with open(sys.argv[1],'r',encoding='utf-8') as f:
    data=json.load(f)
approval = data.get("approval") if isinstance(data,dict) else None
print((approval or {}).get("approvalId",""))
PY
}

verify_step2_inbox() {
  local path="$1"
  local action="$2"
  python3 - <<'PY' "$path" "$action"
import json,sys
path=sys.argv[1]
action=sys.argv[2]
with open(path,'r',encoding='utf-8') as f:
    data=json.load(f)
if not data.get("ok") or data.get("action") != action:
    print(data); sys.exit(1)
inbox=data.get("inbox")
if not isinstance(inbox, dict) or not inbox.get("inboxFile"):
    print(data); sys.exit(1)
print(f"[telegram-inline] {action} two-step callback ok")
PY
}

for action in clio_save hermes_deep_dive minerva_insight; do
  prefix="/tmp/telegram_inline_${action}"
  post_callback "${action}:${EVENT_ID}" "cbq-${action}-s0" "${prefix}-s0.json"
  approval_id="$(extract_approval_id "${prefix}-s0.json")"
  if [[ -z "$approval_id" ]]; then
    echo "[telegram-inline] approvalId missing action=${action}" >&2
    cat "${prefix}-s0.json" >&2 || true
    exit 1
  fi

  echo "[telegram-inline] callback ${action} (step1 yes)"
  post_callback "${action}:approve1_yes_${approval_id}" "cbq-${action}-s1" "${prefix}-s1.json"
  echo "[telegram-inline] callback ${action} (step2 yes)"
  post_callback "${action}:approve2_yes_${approval_id}" "cbq-${action}-s2" "${prefix}-s2.json"
  verify_step2_inbox "${prefix}-s2.json" "${action}"
done

echo "[telegram-inline] PASS"
