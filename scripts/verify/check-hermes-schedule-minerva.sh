#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

FRONTEND_PORT="${FRONTEND_PORT:-3000}"
EXPECTED_TIMEZONE="${EXPECTED_TIMEZONE:-Asia/Seoul}"

get_env() {
  local key="$1"
  local value
  value="$(grep -E "^${key}=" .env.local 2>/dev/null | tail -n 1 | cut -d= -f2- || true)"
  printf '%s' "$value"
}

GENERIC_TZ="$(get_env GENERIC_TIMEZONE)"
N8N_DEFAULT_TZ="$(get_env N8N_DEFAULT_TIMEZONE)"
TZ_VALUE="$(get_env TZ)"

if [[ "$GENERIC_TZ" != "$EXPECTED_TIMEZONE" || "$N8N_DEFAULT_TZ" != "$EXPECTED_TIMEZONE" || "$TZ_VALUE" != "$EXPECTED_TIMEZONE" ]]; then
  echo "[hermes-schedule] FAIL timezone env mismatch. expected=${EXPECTED_TIMEZONE}" >&2
  echo "[hermes-schedule] GENERIC_TIMEZONE=${GENERIC_TZ:-<unset>}" >&2
  echo "[hermes-schedule] N8N_DEFAULT_TIMEZONE=${N8N_DEFAULT_TZ:-<unset>}" >&2
  echo "[hermes-schedule] TZ=${TZ_VALUE:-<unset>}" >&2
  exit 1
fi

ORCH_STATUS="$(
  curl -sS -o /dev/null -w '%{http_code}' \
    "http://127.0.0.1:${FRONTEND_PORT}/" \
    || true
)"
if [[ "$ORCH_STATUS" == "000" ]]; then
  echo "[hermes-schedule] FAIL Next orchestration endpoint unavailable on 127.0.0.1:${FRONTEND_PORT}" >&2
  echo "[hermes-schedule] run: npm run dev -- --hostname 127.0.0.1 --port ${FRONTEND_PORT}" >&2
  exit 1
fi

echo "[hermes-schedule] bootstrap Hermes Daily Briefing workflow"
bash scripts/n8n/bootstrap-hermes-daily-briefing.sh

echo "[hermes-schedule] run schedule/webhook -> orchestration -> minerva dispatch verification"
HERMES_EXPECT_ORCHESTRATION=true \
HERMES_DISPATCH_TO_MINERVA=true \
FRONTEND_PORT="$FRONTEND_PORT" \
bash scripts/n8n/test-hermes-daily-briefing-workflow.sh

echo "[hermes-schedule] PASS"
