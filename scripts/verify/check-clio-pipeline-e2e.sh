#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

FRONTEND_PORT="${FRONTEND_PORT:-3000}"
BASE_URL="http://127.0.0.1:${FRONTEND_PORT}"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$RANDOM"
REBUILD_AGENT="${CLIO_E2E_REBUILD_AGENT:-false}"

count_verified_files() {
  python3 - <<'PY'
from pathlib import Path
root = Path("shared_data/verified_inbox")
if not root.exists():
    print(0)
else:
    print(len(list(root.glob("*.json"))))
PY
}

latest_verified_file() {
  python3 - <<'PY'
from pathlib import Path
root = Path("shared_data/verified_inbox")
files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime)
print(files[-1] if files else "")
PY
}

echo "[clio-e2e] ensure nanoclaw-agent is running"
if [[ "$REBUILD_AGENT" == "true" ]]; then
  echo "[clio-e2e] rebuilding nanoclaw-agent image"
  docker compose build nanoclaw-agent >/dev/null
fi
docker compose up -d nanoclaw-agent >/dev/null

BEFORE_COUNT="$(count_verified_files)"
echo "[clio-e2e] verified_inbox before=${BEFORE_COUNT}"

TOPIC_KEY="hermes-impact-${RUN_ID}"
EVENT_STATUS="$(
  curl -sS -o /tmp/clio_e2e_event.json -w '%{http_code}' \
    -X POST "${BASE_URL}/api/orchestration/events" \
    -H 'content-type: application/json' \
    -d "{
      \"schemaVersion\":1,
      \"agentId\":\"hermes\",
      \"topicKey\":\"${TOPIC_KEY}\",
      \"title\":\"High Impact Research Signal ${RUN_ID}\",
      \"summary\":\"New AI research trend with strong production implication https://example.com/paper-${RUN_ID}\",
      \"priority\":\"high\",
      \"confidence\":0.91,
      \"impactScore\":0.94,
      \"tags\":[\"research\",\"paper\",\"trend\"],
      \"sourceRefs\":[
        {\"title\":\"Paper Source\",\"url\":\"https://example.com/paper-${RUN_ID}\"},
        {\"title\":\"Analysis Source\",\"url\":\"https://example.com/analysis-${RUN_ID}\"}
      ],
      \"forceDispatch\":true
    }" || true
)"

if [[ "$EVENT_STATUS" != "200" ]]; then
  echo "[clio-e2e] orchestration event failed status=${EVENT_STATUS}" >&2
  cat /tmp/clio_e2e_event.json >&2 || true
  exit 1
fi

python3 - <<'PY'
import json
data = json.load(open("/tmp/clio_e2e_event.json", "r", encoding="utf-8"))
auto = data.get("autoClio", {})
assert data.get("ok") is True, "event response ok=false"
assert auto.get("created") is True, f"autoClio not created: {auto}"
print("[clio-e2e] autoClio created:", auto.get("inboxFile"))
PY

echo "[clio-e2e] wait for watchdog output"
AFTER_COUNT="$BEFORE_COUNT"
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  AFTER_COUNT="$(count_verified_files)"
  if [[ "$AFTER_COUNT" -gt "$BEFORE_COUNT" ]]; then
    break
  fi
  sleep 1
done

if [[ "$AFTER_COUNT" -le "$BEFORE_COUNT" ]]; then
  echo "[clio-e2e] verified_inbox did not increase" >&2
  ls -1 shared_data/verified_inbox >&2 || true
  exit 1
fi

LATEST_FILE="$(latest_verified_file)"
if [[ -z "$LATEST_FILE" ]]; then
  echo "[clio-e2e] latest verified file not found" >&2
  exit 1
fi

echo "[clio-e2e] latest verified file: $LATEST_FILE"
cat "$LATEST_FILE"

python3 - <<'PY' "$LATEST_FILE"
import json
import sys
from pathlib import Path

target = Path(sys.argv[1])
payload = json.loads(target.read_text(encoding="utf-8"))
assert payload.get("agent_id") == "clio", "verified payload agent_id is not clio"
assert isinstance(payload.get("tags"), list) and payload["tags"], "tags missing"
assert isinstance(payload.get("source_urls"), list), "source_urls missing"
assert "deepl" in payload, "deepl block missing"
assert "notebooklm" in payload and payload["notebooklm"].get("ready") is True, "notebooklm ready missing"
print("[clio-e2e] verified payload schema validated")
PY

echo "[clio-e2e] PASS"
