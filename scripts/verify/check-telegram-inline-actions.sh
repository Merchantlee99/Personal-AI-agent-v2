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

post_callback() {
  local action="$1"
  local output="$2"
  cat > /tmp/telegram_inline_callback.json <<JSON
{
  "update_id": 1,
  "callback_query": {
    "id": "cbq-${action}",
    "data": "${action}:${EVENT_ID}",
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
    echo "[telegram-inline] callback failed action=${action} status=${status}" >&2
    cat "$output" >&2 || true
    exit 1
  fi
}

echo "[telegram-inline] callback clio_save"
post_callback "clio_save" "/tmp/telegram_inline_clio.json"
python3 - <<'PY'
import json,sys
with open('/tmp/telegram_inline_clio.json','r',encoding='utf-8') as f:
    data=json.load(f)
if not data.get("ok") or data.get("action") != "clio_save":
    print(data)
    sys.exit(1)
if not isinstance(data.get("inbox"), dict) or not data["inbox"].get("inboxFile"):
    print(data)
    sys.exit(1)
print("[telegram-inline] clio_save callback ok")
PY

echo "[telegram-inline] callback hermes_deep_dive"
post_callback "hermes_deep_dive" "/tmp/telegram_inline_hermes.json"
python3 - <<'PY'
import json,sys
with open('/tmp/telegram_inline_hermes.json','r',encoding='utf-8') as f:
    data=json.load(f)
if not data.get("ok") or data.get("action") != "hermes_deep_dive":
    print(data)
    sys.exit(1)
if not isinstance(data.get("inbox"), dict) or not data["inbox"].get("inboxFile"):
    print(data)
    sys.exit(1)
print("[telegram-inline] hermes_deep_dive callback ok")
PY

echo "[telegram-inline] callback minerva_insight"
post_callback "minerva_insight" "/tmp/telegram_inline_minerva.json"
python3 - <<'PY'
import json,sys
with open('/tmp/telegram_inline_minerva.json','r',encoding='utf-8') as f:
    data=json.load(f)
if not data.get("ok") or data.get("action") != "minerva_insight":
    print(data)
    sys.exit(1)
if not isinstance(data.get("inbox"), dict) or not data["inbox"].get("inboxFile"):
    print(data)
    sys.exit(1)
print("[telegram-inline] minerva_insight callback ok")
PY

echo "[telegram-inline] PASS"
