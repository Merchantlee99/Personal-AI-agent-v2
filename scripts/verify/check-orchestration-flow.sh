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

FRONTEND_PORT="${FRONTEND_PORT:-3032}"
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

echo "[orchestration] send sample event"
event_status="$(
  curl -sS -o /tmp/orch_event.json -w '%{http_code}' \
    -X POST "${BASE_URL}/api/orchestration/events" \
    -H 'content-type: application/json' \
    -d '{
      "agentId":"hermes",
      "topicKey":"mobility-market",
      "title":"모빌리티 시장 연결 신호",
      "summary":"로보택시, 배터리 공급망, 핵심 반도체 공급의 상호 영향이 관측되었습니다.",
      "priority":"high",
      "confidence":0.86,
      "tags":["mobility","supply-chain"],
      "sourceRefs":[
        {"title":"Sample A","url":"https://example.com/a"},
        {"title":"Sample B","url":"https://example.com/b"}
      ]
    }' || true
)"

if [[ "$event_status" != "200" ]]; then
  echo "[orchestration] event endpoint failed status=$event_status" >&2
  cat /tmp/orch_event.json >&2 || true
  exit 1
fi
cat /tmp/orch_event.json

event_id="$(python3 - <<'PY'
import json
with open('/tmp/orch_event.json','r',encoding='utf-8') as f:
    data = json.load(f)
print(data.get("eventId",""))
PY
)"
if [[ -z "$event_id" ]]; then
  echo "[orchestration] eventId missing" >&2
  exit 1
fi

echo "[orchestration] trigger clio_save callback"
cat >/tmp/orch_callback_request.json <<JSON
{
  "update_id": 1,
  "callback_query": {
    "id": "cbq-test",
    "data": "clio_save:${event_id}",
    "from": {
      "id": ${SOURCE_USER_ID},
      "username": "nanoclaw-test-user"
    },
    "message": {
      "chat": {
        "id": ${SOURCE_CHAT_ID},
        "type": "private"
      }
    }
  }
}
JSON

callback_headers=(-H 'content-type: application/json')
if [[ -n "$WEBHOOK_SECRET" ]]; then
  callback_headers+=(-H "x-telegram-bot-api-secret-token: ${WEBHOOK_SECRET}")
fi

callback_status="$(
  curl -sS -o /tmp/orch_callback.json -w '%{http_code}' \
    -X POST "${BASE_URL}/api/telegram/webhook" \
    "${callback_headers[@]}" \
    -d @/tmp/orch_callback_request.json || true
)"
if [[ "$callback_status" != "200" ]]; then
  echo "[orchestration] callback endpoint failed status=$callback_status" >&2
  cat /tmp/orch_callback.json >&2 || true
  exit 1
fi
cat /tmp/orch_callback.json

echo "[orchestration] PASS"
