#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

SOURCE_JSON="${1:-/tmp/hermes_first.json}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
ORCH_URL="${ORCH_URL:-http://127.0.0.1:${FRONTEND_PORT}/api/orchestration/events}"

if [[ ! -f "$SOURCE_JSON" ]]; then
  echo "[dispatch-hermes] source json not found: $SOURCE_JSON" >&2
  exit 1
fi

python3 - <<'PY' "$SOURCE_JSON" >/tmp/hermes_orchestration_payload.json
import json
import re
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
data = json.loads(source_path.read_text(encoding="utf-8"))

query = str(data.get("query") or "hermes-briefing").strip()
topic_key = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:64] or "hermes-briefing"
summary = str(data.get("summary") or "Hermes briefing generated").strip()
items = data.get("items") or []
sources = []
if isinstance(items, list):
    for item in items[:3]:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if title and url:
            sources.append({"title": title, "url": url})

payload = {
    "agentId": "hermes",
    "topicKey": topic_key,
    "title": f"Hermes Briefing: {query[:60]}",
    "summary": summary,
    "priority": "high" if not data.get("skipped") else "normal",
    "confidence": 0.84 if not data.get("skipped") else 0.55,
    "tags": ["hermes", "trend", "briefing"],
    "sourceRefs": sources,
    "insightHint": "여러 트렌드 신호가 연결된 테마를 우선 검토하세요.",
}
print(json.dumps(payload, ensure_ascii=False))
PY

status="$(
  curl -sS -o /tmp/hermes_orchestration_result.json -w '%{http_code}' \
    -X POST "$ORCH_URL" \
    -H 'content-type: application/json' \
    -d @/tmp/hermes_orchestration_payload.json || true
)"

if [[ "$status" != "200" ]]; then
  echo "[dispatch-hermes] failed status=$status endpoint=$ORCH_URL" >&2
  cat /tmp/hermes_orchestration_result.json >&2 || true
  exit 1
fi

echo "[dispatch-hermes] orchestration event published"
cat /tmp/hermes_orchestration_result.json

