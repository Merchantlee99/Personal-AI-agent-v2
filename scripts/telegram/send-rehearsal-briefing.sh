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
CHAT_ID="${TELEGRAM_CHAT_ID:-}"

if [[ -z "$CHAT_ID" ]]; then
  echo "[telegram-rehearsal] TELEGRAM_CHAT_ID is required" >&2
  exit 1
fi

TOPIC_KEY="telegram-live-rehearsal-$(date +%s)"

echo "[telegram-rehearsal] send Minerva dispatch event"
status="$(
  curl -sS -o /tmp/telegram_rehearsal_send.json -w '%{http_code}' \
    -X POST "${BASE_URL}/api/orchestration/events" \
    -H 'content-type: application/json' \
    -d "{
      \"schemaVersion\":1,
      \"agentId\":\"hermes\",
      \"topicKey\":\"${TOPIC_KEY}\",
      \"title\":\"[리허설] Hermes 브리핑 전달 테스트\",
      \"summary\":\"텔레그램 인라인 버튼 동작 검증을 위한 테스트 브리핑입니다.\",
      \"priority\":\"high\",
      \"confidence\":0.95,
      \"impactScore\":0.92,
      \"tags\":[\"research\",\"insight\",\"rehearsal\"],
      \"insightHint\":\"핵심 신호를 묶어 Minerva가 액션 버튼으로 후속 연결을 제안합니다.\",
      \"sourceRefs\":[
        {\"title\":\"Sample Research\",\"url\":\"https://example.com/research\"},
        {\"title\":\"Sample Analysis\",\"url\":\"https://example.com/analysis\"}
      ],
      \"chatId\":\"${CHAT_ID}\",
      \"forceDispatch\":true
    }" || true
)"

if [[ "$status" != "200" ]]; then
  echo "[telegram-rehearsal] request failed status=${status}" >&2
  cat /tmp/telegram_rehearsal_send.json >&2 || true
  exit 1
fi

cat /tmp/telegram_rehearsal_send.json

python3 - <<'PY'
import json,sys
with open('/tmp/telegram_rehearsal_send.json','r',encoding='utf-8') as f:
    data=json.load(f)
if not data.get("ok"):
    print("[telegram-rehearsal] api returned not ok", file=sys.stderr)
    print(data, file=sys.stderr)
    sys.exit(1)
telegram=data.get("telegram",{})
if not telegram.get("sent"):
    print("[telegram-rehearsal] telegram delivery failed", file=sys.stderr)
    print(data, file=sys.stderr)
    sys.exit(1)
print(f"[telegram-rehearsal] sent eventId={data.get('eventId')}")
PY

echo "[telegram-rehearsal] now click an inline button in Telegram chat."
echo "[telegram-rehearsal] verify side-effects in local files:"
echo "  - shared_data/inbox/*.json (clio_save/hermes_deep_dive/minerva_insight)"
