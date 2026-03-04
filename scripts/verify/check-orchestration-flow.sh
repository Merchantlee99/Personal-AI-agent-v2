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
OCCURRED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "[orchestration] validate contract negative path"
cat >/tmp/orch_event_invalid_request.json <<JSON
{
  "schemaVersion": "1.0",
  "eventType": "hermes.briefing.created",
  "producer": "verify-script",
  "occurredAt": "${OCCURRED_AT}",
  "payload": {
    "agentId": "hermes",
    "topicKey": "invalid-contract",
    "title": "invalid event",
    "priority": "high",
    "confidence": 0.8
  }
}
JSON
invalid_status="$(
  curl -sS -o /tmp/orch_event_invalid.json -w '%{http_code}' \
    -X POST "${BASE_URL}/api/orchestration/events" \
    -H 'content-type: application/json' \
    -d @/tmp/orch_event_invalid_request.json || true
)"
if [[ "$invalid_status" != "400" ]]; then
  echo "[orchestration] expected invalid contract status=400, got $invalid_status" >&2
  cat /tmp/orch_event_invalid.json >&2 || true
  exit 1
fi
python3 - <<'PY'
import json,sys
with open('/tmp/orch_event_invalid.json','r',encoding='utf-8') as f:
    data=json.load(f)
if data.get("error") != "invalid_event_contract":
    print(data)
    sys.exit(1)
errors = data.get("validationErrors")
if not isinstance(errors, list) or len(errors) == 0:
    print(data)
    sys.exit(1)
print("[orchestration] invalid contract rejected as expected")
PY

if [[ "${ORCH_REQUIRE_SCHEMA_V1:-false}" == "true" ]]; then
  echo "[orchestration] validate strict schema requirement"
  legacy_status="$(
    curl -sS -o /tmp/orch_event_legacy.json -w '%{http_code}' \
      -X POST "${BASE_URL}/api/orchestration/events" \
      -H 'content-type: application/json' \
      -d '{
        "agentId":"hermes",
        "topicKey":"legacy-no-schema",
        "title":"legacy event",
        "summary":"legacy payload without schema envelope",
        "priority":"normal",
        "confidence":0.7
      }' || true
  )"
  if [[ "$legacy_status" != "400" ]]; then
    echo "[orchestration] expected strict schema status=400, got $legacy_status" >&2
    cat /tmp/orch_event_legacy.json >&2 || true
    exit 1
  fi
  python3 - <<'PY'
import json,sys
with open('/tmp/orch_event_legacy.json','r',encoding='utf-8') as f:
    data=json.load(f)
if data.get("error") != "schema_version_required":
    print(data)
    sys.exit(1)
print("[orchestration] strict schema gate works")
PY
fi

echo "[orchestration] send sample event"
cat >/tmp/orch_event_request.json <<JSON
{
  "schemaVersion": "1.0",
  "eventType": "hermes.briefing.created",
  "producer": "verify-script",
  "occurredAt": "${OCCURRED_AT}",
  "payload": {
    "agentId": "hermes",
    "topicKey": "mobility-market",
    "title": "모빌리티 시장 연결 신호",
    "summary": "로보택시, 배터리 공급망, 핵심 반도체 공급의 상호 영향이 관측되었습니다.",
    "priority": "high",
    "confidence": 0.86,
    "tags": ["mobility", "supply-chain"],
    "sourceRefs": [
      { "title": "Sample A", "url": "https://example.com/a" },
      { "title": "Sample B", "url": "https://example.com/b" }
    ]
  }
}
JSON
event_status="$(
  curl -sS -o /tmp/orch_event.json -w '%{http_code}' \
    -X POST "${BASE_URL}/api/orchestration/events" \
    -H 'content-type: application/json' \
    -d @/tmp/orch_event_request.json || true
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
callback_headers=(-H 'content-type: application/json')
if [[ -n "$WEBHOOK_SECRET" ]]; then
  callback_headers+=(-H "x-telegram-bot-api-secret-token: ${WEBHOOK_SECRET}")
fi

post_callback() {
  local callback_data="$1"
  local callback_id="$2"
  local output="$3"
  cat >/tmp/orch_callback_request.json <<JSON
{
  "update_id": 1,
  "callback_query": {
    "id": "${callback_id}",
    "data": "${callback_data}",
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

  local callback_status
  callback_status="$(
    curl -sS -o "${output}" -w '%{http_code}' \
      -X POST "${BASE_URL}/api/telegram/webhook" \
      "${callback_headers[@]}" \
      -d @/tmp/orch_callback_request.json || true
  )"
  if [[ "$callback_status" != "200" ]]; then
    echo "[orchestration] callback endpoint failed status=$callback_status data=${callback_data}" >&2
    cat "${output}" >&2 || true
    exit 1
  fi
}

post_callback "clio_save:${event_id}" "cbq-stage0" "/tmp/orch_callback.json"
cat /tmp/orch_callback.json

approval_id="$(python3 - <<'PY'
import json
with open('/tmp/orch_callback.json','r',encoding='utf-8') as f:
    data=json.load(f)
approval = data.get("approval") if isinstance(data,dict) else None
print((approval or {}).get("approvalId",""))
PY
)"
if [[ -z "$approval_id" ]]; then
  echo "[orchestration] approvalId missing after initial callback" >&2
  cat /tmp/orch_callback.json >&2 || true
  exit 1
fi

post_callback "clio_save:approve1_yes_${approval_id}" "cbq-stage1" "/tmp/orch_callback_step1.json"
cat /tmp/orch_callback_step1.json

post_callback "clio_save:approve2_yes_${approval_id}" "cbq-stage2" "/tmp/orch_callback_step2.json"
cat /tmp/orch_callback_step2.json

python3 - <<'PY'
import json,sys
with open('/tmp/orch_callback_step2.json','r',encoding='utf-8') as f:
    data=json.load(f)
if not data.get("ok"):
    print(data)
    sys.exit(1)
if data.get("action") != "clio_save":
    print(data)
    sys.exit(1)
inbox = data.get("inbox")
if not isinstance(inbox, dict) or not inbox.get("inboxFile"):
    print(data)
    sys.exit(1)
print("[orchestration] clio_save two-step approval ok")
PY

echo "[orchestration] PASS"
