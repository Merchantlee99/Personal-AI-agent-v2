#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "[hermes-test] bootstrap workflow"
bash scripts/n8n/bootstrap-hermes-daily-briefing.sh >/tmp/hermes_bootstrap.log
cat /tmp/hermes_bootstrap.log

RUN_ID="$(date +%Y%m%d-%H%M%S)-$RANDOM"

PAYLOAD='{
  "query":"2026 한국 AI 트렌드 '"$RUN_ID"'",
  "items":[
    {"title":"Open-source agentic stack release", "url":"https://example.com/a", "snippet":"major release for agent orchestration", "bucket":"HOT"},
    {"title":"Enterprise AI adoption report", "url":"https://example.com/b", "snippet":"new analysis on enterprise readiness", "bucket":"INSIGHT"},
    {"title":"Funding watchlist update", "url":"https://example.com/c", "snippet":"monitoring startup funding signals", "bucket":"MONITOR"}
  ]
}'

echo "[hermes-test] first run (expect skipped=false)"
curl -fsS -X POST "http://localhost:5678/webhook/hermes-daily-briefing" \
  -H 'content-type: application/json' \
  -d "$PAYLOAD" >/tmp/hermes_first.json
cat /tmp/hermes_first.json

python3 -c 'import json,sys; d=json.load(open("/tmp/hermes_first.json")); assert d.get("ok") is True; assert d.get("skipped") is False; assert "Hermes Daily Briefing" in (d.get("briefing_markdown") or ""); print("[hermes-test] first run validated")'

echo "[hermes-test] second run same payload (expect duplicate skip)"
curl -fsS -X POST "http://localhost:5678/webhook/hermes-daily-briefing" \
  -H 'content-type: application/json' \
  -d "$PAYLOAD" >/tmp/hermes_second.json
cat /tmp/hermes_second.json

python3 -c 'import json,sys; d=json.load(open("/tmp/hermes_second.json")); assert d.get("ok") is True; assert d.get("skipped") is True; assert d.get("reason") == "duplicate_briefing"; print("[hermes-test] dedup validated")'

echo "[hermes-test] PASS"
