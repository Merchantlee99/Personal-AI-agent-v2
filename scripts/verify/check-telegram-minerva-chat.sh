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
SOURCE_CHAT_ID="${SOURCE_CHAT_ID:-${TELEGRAM_CHAT_ID:-20001}}"

headers=(-H 'content-type: application/json')
if [[ -n "$WEBHOOK_SECRET" ]]; then
  headers+=(-H "x-telegram-bot-api-secret-token: ${WEBHOOK_SECRET}")
fi

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

  local status
  status="$(
    curl -sS -o "$output" -w '%{http_code}' \
      -X POST "${BASE_URL}/api/telegram/webhook" \
      "${headers[@]}" \
      -d @/tmp/telegram_minerva_message_update.json || true
  )"
  if [[ "$status" != "200" ]]; then
    echo "[telegram-chat] webhook failed status=${status}" >&2
    cat "$output" >&2 || true
    exit 1
  fi
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

python3 - <<'PY' "$BEFORE_COUNT" "$AFTER_COUNT"
import sys

before = int(sys.argv[1])
after = int(sys.argv[2])
if after < before + 2:
    raise SystemExit(f"[telegram-chat] history append failed: before={before} after={after}")
print(f"[telegram-chat] history append ok: before={before} after={after}")
PY

echo "[telegram-chat] PASS"
