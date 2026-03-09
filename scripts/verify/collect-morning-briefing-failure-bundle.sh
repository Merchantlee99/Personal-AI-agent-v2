#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BUNDLE_DIR="shared_data/logs/morning-briefing-failure-${TIMESTAMP}"
mkdir -p "$BUNDLE_DIR"

echo "[morning-bundle] collecting failure bundle into ${BUNDLE_DIR}"

bash scripts/runtime/compose.sh ps > "${BUNDLE_DIR}/compose-ps.txt" 2>&1 || true
bash scripts/runtime/compose.sh logs llm-proxy --tail=200 > "${BUNDLE_DIR}/llm-proxy.log" 2>&1 || true
bash scripts/runtime/compose.sh logs telegram-poller --tail=200 > "${BUNDLE_DIR}/telegram-poller.log" 2>&1 || true
bash scripts/runtime/compose.sh logs n8n --tail=200 > "${BUNDLE_DIR}/n8n.log" 2>&1 || true
bash scripts/runtime/compose.sh logs nanoclaw-agent --tail=200 > "${BUNDLE_DIR}/nanoclaw-agent.log" 2>&1 || true

bash scripts/verify/check-morning-briefing-preflight.sh > "${BUNDLE_DIR}/morning-preflight.log" 2>&1 || true
bash scripts/verify/check-runtime-drift.sh > "${BUNDLE_DIR}/runtime-drift.log" 2>&1 || true
bash scripts/verify/check-telegram-runtime-status.sh > "${BUNDLE_DIR}/telegram-runtime.log" 2>&1 || true
bash scripts/verify/check-hermes-active-schedule.sh > "${BUNDLE_DIR}/hermes-active-schedule.log" 2>&1 || true
bash scripts/verify/report-morning-briefing-observations.sh > "${BUNDLE_DIR}/morning-observation-report.json" 2>&1 || true

if [[ -f shared_data/logs/morning_briefing_observations.jsonl ]]; then
  tail -n 50 shared_data/logs/morning_briefing_observations.jsonl > "${BUNDLE_DIR}/morning-observations.tail.jsonl" || true
fi

if [[ -f shared_data/logs/daily-runtime-check.latest.log ]]; then
  cp shared_data/logs/daily-runtime-check.latest.log "${BUNDLE_DIR}/daily-runtime-check.latest.log" || true
fi

echo "${BUNDLE_DIR}" > shared_data/logs/morning-briefing-failure-bundle.latest
echo "[morning-bundle] latest bundle: ${BUNDLE_DIR}"
