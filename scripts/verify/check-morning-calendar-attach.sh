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

API_PORT="${API_PORT:-8001}"
BASE_URL="http://127.0.0.1:${API_PORT}"
CHAT_ID="${TELEGRAM_CHAT_ID:-}"
TOKEN_FILE="${REPO_ROOT}/shared_data/shared_memory/google_calendar_tokens.json"
GCAL_ENABLED_RAW="${GOOGLE_CALENDAR_ENABLED:-false}"
ATTACH_ENABLED_RAW="${GOOGLE_CALENDAR_ATTACH_TO_MORNING_BRIEFING:-true}"

normalize_bool() {
  local raw
  raw="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | xargs)"
  case "$raw" in
    1|true|yes|on) echo "true" ;;
    *) echo "false" ;;
  esac
}

GCAL_ENABLED="$(normalize_bool "$GCAL_ENABLED_RAW")"
ATTACH_ENABLED="$(normalize_bool "$ATTACH_ENABLED_RAW")"

GCAL_CONNECTED="$(
  python3 - <<'PY' "$TOKEN_FILE"
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if not path.exists():
    print("false")
    raise SystemExit(0)
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("false")
    raise SystemExit(0)
print("true" if isinstance(payload, dict) and payload.get("accessToken") else "false")
PY
)"

if [[ "$GCAL_ENABLED" != "true" || "$ATTACH_ENABLED" != "true" || "$GCAL_CONNECTED" != "true" ]]; then
  echo "[morning-gcal] skip: enabled=${GCAL_ENABLED} attach=${ATTACH_ENABLED} connected=${GCAL_CONNECTED}"
  exit 0
fi

echo "[morning-gcal] send forced morning event"
status="$(
  curl -sS -o /tmp/morning_gcal_attach.json -w '%{http_code}' \
    -X POST "${BASE_URL}/api/orchestration/events" \
    -H 'content-type: application/json' \
    -d "{
      \"schemaVersion\": 1,
      \"agentId\": \"hermes\",
      \"topicKey\": \"calendar-attach-check\",
      \"title\": \"Morning calendar attach verification\",
      \"summary\": \"Calendar attach regression test\",
      \"priority\": \"high\",
      \"confidence\": 0.91,
      \"forceDispatch\": true,
      \"forceTheme\": \"morning_briefing\",
      \"chatId\": \"${CHAT_ID}\",
      \"sourceRefs\": [
        {\"title\": \"Calendar attach check\", \"url\": \"https://example.com/calendar-check\"}
      ]
    }" || true
)"

if [[ "$status" != "200" ]]; then
  echo "[morning-gcal] orchestration failed status=${status}" >&2
  cat /tmp/morning_gcal_attach.json >&2 || true
  exit 1
fi

python3 - <<'PY'
import json

with open('/tmp/morning_gcal_attach.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

if data.get('theme') != 'morning_briefing':
    print(data)
    raise SystemExit('[morning-gcal] expected morning_briefing theme')
if data.get('calendarBriefingAttached') is not True:
    print(data)
    raise SystemExit('[morning-gcal] expected calendarBriefingAttached=true')

print('[morning-gcal] morning calendar attach ok')
PY

echo "[morning-gcal] PASS"
