#!/usr/bin/env bash
set -euo pipefail

FRONTEND_PORT="${FRONTEND_PORT:-3032}"
BASE_URL="http://127.0.0.1:${FRONTEND_PORT}"

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
callback_status="$(
  curl -sS -o /tmp/orch_callback.json -w '%{http_code}' \
    -X POST "${BASE_URL}/api/telegram/webhook" \
    -H 'content-type: application/json' \
    -d "{
      \"update_id\": 1,
      \"callback_query\": {
        \"id\": \"cbq-test\",
        \"data\": \"clio_save:${event_id}\"
      }
    }" || true
)"
if [[ "$callback_status" != "200" ]]; then
  echo "[orchestration] callback endpoint failed status=$callback_status" >&2
  cat /tmp/orch_callback.json >&2 || true
  exit 1
fi
cat /tmp/orch_callback.json

echo "[orchestration] PASS"

