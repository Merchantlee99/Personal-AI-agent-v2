#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "[hermes-test] validate schedule triggers exist in workflow json"
python3 - <<'PY'
import json
from pathlib import Path
workflow = json.loads(Path("n8n/workflows/hermes-daily-briefing.json").read_text(encoding="utf-8"))
names = {node.get("name") for node in workflow.get("nodes", [])}
required = {
    "Schedule P0 Daily (KST 09:00)",
    "Schedule P1 Every2Days (KST 09:10)",
    "Schedule P2 Every3Days (KST 09:20)",
    "Prepare P0 Config",
    "Prepare P1 Config",
    "Prepare P2 Config",
    "Collect Tier Signals",
    "Build Briefing Summary",
    "Build Orchestration Payload",
    "Publish Orchestration Event",
}
missing = sorted(required - names)
if missing:
    raise SystemExit(f"missing schedule nodes: {', '.join(missing)}")
code_by_name = {
    node.get("name"): str(node.get("parameters", {}).get("jsCode", ""))
    for node in workflow.get("nodes", [])
    if node.get("type") == "n8n-nodes-base.code"
}
for node_name in ("Normalize Input", "Collect Tier Signals"):
    code = code_by_name.get(node_name, "")
    if "INJECTION_PATTERNS" not in code or "isSafeUrl" not in code:
        raise SystemExit(f"missing injection/url filter in node: {node_name}")
collector_code = code_by_name.get("Collect Tier Signals", "")
if "HERMES_SEARCH_PROVIDER" not in collector_code:
    raise SystemExit("missing HERMES_SEARCH_PROVIDER routing in shared collector")
if "TAVILY_API_KEY" not in collector_code:
    raise SystemExit("missing TAVILY_API_KEY usage in shared collector")
for node_name in ("Prepare P0 Config", "Prepare P1 Config", "Prepare P2 Config"):
    code = code_by_name.get(node_name, "")
    if "query_base" not in code or "tier_domains" not in code or "heartbeat_url" not in code:
        raise SystemExit(f"missing tier config fields in node: {node_name}")
print("[hermes-test] schedule + security filter nodes verified")
PY

echo "[hermes-test] bootstrap workflow"
bash scripts/n8n/bootstrap-hermes-daily-briefing.sh >/tmp/hermes_bootstrap.log
cat /tmp/hermes_bootstrap.log

RUN_ID="$(date +%Y%m%d-%H%M%S)-$RANDOM"
HERMES_EXPECT_ORCHESTRATION="${HERMES_EXPECT_ORCHESTRATION:-false}"

count_orchestration_events() {
  python3 - <<'PY'
import json
from pathlib import Path
path = Path("shared_data/shared_memory/agent_events.json")
if not path.exists():
    print(0)
else:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        print(len(data) if isinstance(data, list) else 0)
    except Exception:
        print(0)
PY
}

EVENT_COUNT_BEFORE=0
if [[ "$HERMES_EXPECT_ORCHESTRATION" == "true" ]]; then
  EVENT_COUNT_BEFORE="$(count_orchestration_events)"
  echo "[hermes-test] orchestration events before=${EVENT_COUNT_BEFORE}"
fi

PAYLOAD='{
  "query":"2026 한국 AI 트렌드 '"$RUN_ID"'",
  "items":[
    {"title":"Open-source agentic stack release", "url":"https://example.com/a", "snippet":"major release for agent orchestration", "bucket":"HOT"},
    {"title":"Enterprise AI adoption report", "url":"https://example.com/b", "snippet":"new analysis on enterprise readiness", "bucket":"INSIGHT"},
    {"title":"Funding watchlist update", "url":"https://example.com/c", "snippet":"monitoring startup funding signals", "bucket":"MONITOR"}
  ]
}'

post_with_retry() {
  local outfile="$1"
  local attempt=1
  local max_attempts=5

  while (( attempt <= max_attempts )); do
    curl -fsS -X POST "http://localhost:5678/webhook/hermes-daily-briefing" \
      -H 'content-type: application/json' \
      -d "$PAYLOAD" >"$outfile"

    if python3 - "$outfile" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
raw = path.read_text(encoding="utf-8").strip()
if not raw:
    raise SystemExit(1)
payload = json.loads(raw)
if not isinstance(payload, dict) or payload.get("ok") is not True:
    raise SystemExit(1)
PY
    then
      return 0
    fi

    echo "[hermes-test] retry ${attempt}/${max_attempts} after empty or invalid response"
    sleep 2
    attempt=$((attempt + 1))
  done

  echo "[hermes-test] failed to get valid JSON response from hermes-daily-briefing webhook" >&2
  return 1
}

echo "[hermes-test] first run (expect skipped=false)"
post_with_retry /tmp/hermes_first.json
cat /tmp/hermes_first.json

python3 -c 'import json,sys; d=json.load(open("/tmp/hermes_first.json")); assert d.get("ok") is True; assert d.get("skipped") is False; assert "Hermes Daily Briefing" in (d.get("briefing_markdown") or ""); print("[hermes-test] first run validated")'
python3 -c 'import json; d=json.load(open("/tmp/hermes_first.json")); o=d.get("orchestration",{}); assert o.get("attempted") in (True, False); print("[hermes-test] orchestration field present:", o.get("attempted"))'
if [[ "$HERMES_EXPECT_ORCHESTRATION" == "true" ]]; then
  python3 - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path("/tmp/hermes_first.json").read_text(encoding="utf-8"))
orch = payload.get("orchestration", {}) if isinstance(payload, dict) else {}

attempted = bool(orch.get("attempted"))
status = orch.get("status")
ok = orch.get("ok")

if not attempted:
    raise SystemExit("[hermes-test] expected orchestration.attempted=true")
if not isinstance(status, int) or status < 200 or status >= 300:
    raise SystemExit(f"[hermes-test] expected orchestration status 2xx, got status={status!r}")
if ok is not True:
    raise SystemExit(f"[hermes-test] expected orchestration.ok=true, got ok={ok!r}")

print("[hermes-test] orchestration status validated")
PY
fi

echo "[hermes-test] second run same payload (expect duplicate skip)"
post_with_retry /tmp/hermes_second.json
cat /tmp/hermes_second.json

python3 -c 'import json,sys; d=json.load(open("/tmp/hermes_second.json")); assert d.get("ok") is True; assert d.get("skipped") is True; assert d.get("reason") == "duplicate_briefing"; print("[hermes-test] dedup validated")'

if [[ "$HERMES_EXPECT_ORCHESTRATION" == "true" ]]; then
  sleep 1
  EVENT_COUNT_AFTER="$(count_orchestration_events)"
  echo "[hermes-test] orchestration events after=${EVENT_COUNT_AFTER}"
  if (( EVENT_COUNT_AFTER <= EVENT_COUNT_BEFORE )); then
    echo "[hermes-test] expected orchestration event count to increase" >&2
    exit 1
  fi
fi

if [[ "${HERMES_DISPATCH_TO_MINERVA:-false}" == "true" ]]; then
  echo "[hermes-test] dispatch first briefing to minerva orchestration"
  API_PORT="${API_PORT:-8001}" bash scripts/n8n/dispatch-hermes-briefing-to-minerva.sh /tmp/hermes_first.json
fi

echo "[hermes-test] PASS"
