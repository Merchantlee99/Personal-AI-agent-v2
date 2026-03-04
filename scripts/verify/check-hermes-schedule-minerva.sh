#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

FRONTEND_PORT="${FRONTEND_PORT:-3000}"

echo "[hermes-schedule] bootstrap Hermes Daily Briefing workflow"
bash scripts/n8n/bootstrap-hermes-daily-briefing.sh

echo "[hermes-schedule] run schedule/webhook -> orchestration -> minerva dispatch verification"
HERMES_EXPECT_ORCHESTRATION=true \
HERMES_DISPATCH_TO_MINERVA=true \
FRONTEND_PORT="$FRONTEND_PORT" \
bash scripts/n8n/test-hermes-daily-briefing-workflow.sh

echo "[hermes-schedule] PASS"
