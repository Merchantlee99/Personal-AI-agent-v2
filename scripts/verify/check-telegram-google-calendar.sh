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
GOOGLE_ENABLED="$(get_env GOOGLE_CALENDAR_ENABLED)"

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

post_message_update() {
  local text="$1"
  local output="$2"
  local text_json
  text_json="$(python3 - <<'PY' "$text"
import json
import sys
print(json.dumps(sys.argv[1], ensure_ascii=False))
PY
)"

  cat >/tmp/telegram_gcal_update.json <<JSON
{
  "update_id": 930001,
  "message": {
    "message_id": 89001,
    "text": ${text_json},
    "from": { "id": ${SOURCE_USER_ID}, "username": "nanoclaw-gcal-test" },
    "chat": { "id": ${SOURCE_CHAT_ID}, "type": "private" }
  }
}
JSON

  local status
  status="$(post_update_via_proxy /tmp/telegram_gcal_update.json "$output" "$WEBHOOK_SECRET" || true)"
  if [[ "$status" != "200" ]]; then
    echo "[telegram-gcal] webhook failed status=${status}" >&2
    cat "$output" >&2 || true
    exit 1
  fi
}

echo "[telegram-gcal] send /gcal_status"
post_message_update "/gcal_status" "/tmp/telegram_gcal_status.json"
python3 - <<'PY'
import json
with open('/tmp/telegram_gcal_status.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
if not data.get('ok') or data.get('mode') != 'message_text' or data.get('command') != '/gcal_status':
    print(data)
    raise SystemExit(1)
if not isinstance(data.get('status'), dict):
    print(data)
    raise SystemExit(1)
print('[telegram-gcal] /gcal_status ok')
PY

GCAL_CONNECTED="$(
  python3 - <<'PY'
import json
with open('/tmp/telegram_gcal_status.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
status = data.get('status') or {}
print('true' if bool(status.get('connected')) else 'false')
PY
)"

google_enabled_lower="$(printf '%s' "$GOOGLE_ENABLED" | tr '[:upper:]' '[:lower:]')"
if [[ "$google_enabled_lower" == "true" ]]; then
  echo "[telegram-gcal] send /gcal_connect"
  post_message_update "/gcal_connect" "/tmp/telegram_gcal_connect.json"
  python3 - <<'PY'
import json
with open('/tmp/telegram_gcal_connect.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
if not data.get('ok') or data.get('mode') != 'message_text' or data.get('command') != '/gcal_connect':
    print(data)
    raise SystemExit(1)
url = str(data.get('authorizationUrl') or '')
if 'accounts.google.com/o/oauth2' not in url:
    print(data)
    raise SystemExit(1)
print('[telegram-gcal] /gcal_connect ok')
PY
else
  echo "[telegram-gcal] GOOGLE_CALENDAR_ENABLED=false -> /gcal_connect 검증 생략"
fi

if [[ "$GCAL_CONNECTED" == "true" ]]; then
  echo "[telegram-gcal] send /gcal_today"
  post_message_update "/gcal_today" "/tmp/telegram_gcal_today.json"
  python3 - <<'PY'
import json
with open('/tmp/telegram_gcal_today.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
if not data.get('ok') or data.get('mode') != 'message_text' or data.get('command') != '/gcal_today':
    print(data)
    raise SystemExit(1)
if not isinstance(data.get('today'), dict):
    print(data)
    raise SystemExit(1)
print('[telegram-gcal] /gcal_today ok')
PY
else
  echo "[telegram-gcal] calendar not connected -> /gcal_today 검증 생략"
fi

echo "[telegram-gcal] PASS"
