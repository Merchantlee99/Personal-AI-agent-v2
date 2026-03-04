#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

FRONTEND_PORT="${FRONTEND_PORT:-3000}"
BASE_URL="${FRONTEND_BASE_URL:-http://127.0.0.1:${FRONTEND_PORT}}"
MEMORY_FILE="shared_data/shared_memory/memory.md"
EVENT_TMP="/tmp/memory_md_event.json"

echo "[memory-md] ensure frontend is reachable (${BASE_URL})"
for _ in {1..30}; do
  if curl -sS -o /dev/null "${BASE_URL}/api/chat"; then
    break
  fi
  sleep 1
done

echo "[memory-md] send orchestration event"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$RANDOM"
rm -f "${EVENT_TMP}"
touch "${EVENT_TMP}"
status="000"
for _ in {1..15}; do
  status="$(
    curl -sS --connect-timeout 2 --max-time 10 -o "${EVENT_TMP}" -w '%{http_code}' \
      -X POST "${BASE_URL}/api/orchestration/events" \
      -H 'content-type: application/json' \
      -d "{
          \"agentId\":\"hermes\",
        \"topicKey\":\"runtime-memory-check-${RUN_ID}\",
        \"title\":\"Runtime Memory Check ${RUN_ID}\",
        \"summary\":\"runtime memory 기록 정책 검증 이벤트\",
        \"priority\":\"normal\",
        \"confidence\":0.62,
        \"tags\":[\"memory\",\"ops\"],
        \"sourceRefs\":[{\"title\":\"Memory Check Source\",\"url\":\"https://example.com/memory-check\"}]
      }" || true
  )"
  if [[ "$status" == "200" ]]; then
    break
  fi
  sleep 1
done

if [[ "$status" != "200" ]]; then
  echo "[memory-md] orchestration request failed status=${status}" >&2
  if [[ -s "${EVENT_TMP}" ]]; then
    cat "${EVENT_TMP}" >&2 || true
  else
    echo "[memory-md] no response body captured" >&2
  fi
  exit 1
fi

EVENT_ID="$(python3 - <<'PY'
import json
data = json.load(open("/tmp/memory_md_event.json", "r", encoding="utf-8"))
print(data.get("eventId", ""))
PY
)"

if [[ -z "$EVENT_ID" ]]; then
  echo "[memory-md] missing eventId in orchestration response" >&2
  cat /tmp/memory_md_event.json >&2 || true
  exit 1
fi

if [[ ! -f "$MEMORY_FILE" ]]; then
  echo "[memory-md] missing file: $MEMORY_FILE" >&2
  exit 1
fi

if ! rg -q "$EVENT_ID" "$MEMORY_FILE"; then
  echo "[memory-md] event not found in memory.md (event_id=$EVENT_ID)" >&2
  tail -n 80 "$MEMORY_FILE" >&2 || true
  exit 1
fi

echo "[memory-md] file=${MEMORY_FILE}"
echo "[memory-md] event_id=${EVENT_ID}"
echo "[memory-md] PASS"
