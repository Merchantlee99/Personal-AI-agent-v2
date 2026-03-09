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
HISTORY_FILE="${REPO_ROOT}/shared_data/shared_memory/telegram_chat_history.json"

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

history_count_for_chat() {
  python3 - <<'PY' "$HISTORY_FILE" "$SOURCE_CHAT_ID"
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
chat_id = sys.argv[2]
if not path.exists():
    print(0)
    raise SystemExit(0)
try:
    data = json.loads(path.read_text(encoding='utf-8'))
except Exception:
    print(0)
    raise SystemExit(0)
rows = data.get(chat_id, [])
print(len(rows) if isinstance(rows, list) else 0)
PY
}

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

  cat >/tmp/telegram_minerva_message_update.json <<JSON
{
  "update_id": 920001,
  "message": {
    "message_id": 88001,
    "text": ${text_json},
    "from": { "id": ${SOURCE_USER_ID}, "username": "nanoclaw-minerva-chat-test" },
    "chat": { "id": ${SOURCE_CHAT_ID}, "type": "private" }
  }
}
JSON

  : >"$output"
  local status=""
  for _ in 1 2 3; do
    status="$(post_update_via_proxy /tmp/telegram_minerva_message_update.json "$output" "$WEBHOOK_SECRET" || true)"
    if [[ "$status" == "200" ]]; then
      return 0
    fi
    sleep 1
  done
  echo "[telegram-chat] webhook failed status=${status}" >&2
  cat "$output" >&2 || true
  exit 1
}

echo "[telegram-chat] before history count"
BEFORE_COUNT="$(history_count_for_chat)"
echo "[telegram-chat] before_count=${BEFORE_COUNT}"

echo "[telegram-chat] send /help command"
post_message_update "/help" "/tmp/telegram_minerva_help.json"
python3 - <<'PY'
import json
import sys

with open('/tmp/telegram_minerva_help.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

if not data.get("ok") or data.get("mode") != "message_text":
    print(data)
    raise SystemExit(1)

if data.get("command") != "/help":
    print(data)
    raise SystemExit(1)

print("[telegram-chat] help command ok")
PY

echo "[telegram-chat] send normal Minerva message"
post_message_update "미네르바, 오늘 우선순위 3개만 짧게 정리해줘." "/tmp/telegram_minerva_chat.json"
python3 - <<'PY'
import json
import sys

with open('/tmp/telegram_minerva_chat.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

if not data.get("ok") or data.get("mode") != "message_text":
    print(data)
    raise SystemExit(1)

if "minerva" not in data or not isinstance(data.get("minerva"), dict):
    print(data)
    raise SystemExit(1)

if "telegram" not in data or not isinstance(data.get("telegram"), dict):
    print(data)
    raise SystemExit(1)

print("[telegram-chat] message_text path ok")
PY

echo "[telegram-chat] after history count"
AFTER_COUNT="$(history_count_for_chat)"
echo "[telegram-chat] after_count=${AFTER_COUNT}"

TELEGRAM_MINERVA_HISTORY_TURNS_VALUE="$(get_env TELEGRAM_MINERVA_HISTORY_TURNS)"

python3 - <<'PY' "$BEFORE_COUNT" "$AFTER_COUNT" "$TELEGRAM_MINERVA_HISTORY_TURNS_VALUE"
import os
import sys

before = int(sys.argv[1])
after = int(sys.argv[2])
turns = int(sys.argv[3] or "10")
max_entries = max(4, turns * 2)
expected_after = max_entries if before + 2 > max_entries else before + 2
if after < expected_after:
    raise SystemExit(
        f"[telegram-chat] history append failed: before={before} after={after} expected_after>={expected_after}"
    )
print(f"[telegram-chat] history append ok: before={before} after={after} limit={max_entries}")
PY

echo "[telegram-chat] PASS"
