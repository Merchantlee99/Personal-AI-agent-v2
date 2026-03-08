#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

API_PORT="${API_PORT:-8001}"
BASE_URL="http://127.0.0.1:${API_PORT}"
MEMORY_FILE="shared_data/shared_memory/memory.md"

echo "[memory-md] send orchestration event"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$RANDOM"
status="$(
  curl -sS -o /tmp/memory_md_event.json -w '%{http_code}' \
    -X POST "${BASE_URL}/api/orchestration/events" \
    -H 'content-type: application/json' \
    -d "{
        \"schemaVersion\":1,
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

if [[ "$status" != "200" ]]; then
  echo "[memory-md] orchestration request failed status=${status}" >&2
  cat /tmp/memory_md_event.json >&2 || true
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
