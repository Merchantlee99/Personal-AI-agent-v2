#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
source "${REPO_ROOT}/scripts/runtime/load-env.sh"
load_runtime_env "${REPO_ROOT}/.env.local"

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
MODE="${MORNING_GCAL_ATTACH_MODE:-dispatch}"

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

if [[ "$MODE" == "status" ]]; then
  status="$(bash "${REPO_ROOT}/scripts/runtime/internal-api-request.sh" GET "${BASE_URL}/api/integrations/google-calendar/status" /tmp/morning_gcal_status.json || true)"
  if [[ "$status" != "200" ]]; then
    echo "[morning-gcal] status check failed status=${status}" >&2
    cat /tmp/morning_gcal_status.json >&2 || true
    exit 1
  fi
  python3 - <<'PY'
import json
with open('/tmp/morning_gcal_status.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
if data.get('connected') is not True:
    print(data)
    raise SystemExit('[morning-gcal] expected connected=true')
print('[morning-gcal] readonly status ok')
PY
  echo "[morning-gcal] PASS"
  exit 0
fi

echo "[morning-gcal] send forced morning event"
cat >/tmp/morning_gcal_attach.payload.json <<JSON
{
  "schemaVersion": 1,
  "agentId": "hermes",
  "topicKey": "calendar-attach-check",
  "title": "Morning calendar attach verification",
  "summary": "Calendar attach regression test",
  "priority": "high",
  "confidence": 0.91,
  "forceDispatch": true,
  "forceTheme": "morning_briefing",
  "chatId": "${CHAT_ID}",
  "sourceRefs": [
    {"title": "Calendar attach check", "url": "https://example.com/calendar-check"}
  ]
}
JSON
status="$(bash "${REPO_ROOT}/scripts/runtime/internal-api-request.sh" POST "${BASE_URL}/api/orchestration/events" /tmp/morning_gcal_attach.json /tmp/morning_gcal_attach.payload.json || true)"

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
