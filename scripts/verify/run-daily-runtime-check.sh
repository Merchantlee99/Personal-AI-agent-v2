#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

LOG_DIR="shared_data/logs"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${LOG_DIR}/daily-runtime-check-${TIMESTAMP}.log"
LATEST_LINK="${LOG_DIR}/daily-runtime-check.latest.log"
mkdir -p "$LOG_DIR"

checks=(
  "npm run verify:smoke"
  "npm run verify:orchestration"
  "npm run verify:hermes:schedule"
  "npm run verify:telegram:inline"
  "npm run verify:telegram:chat"
  "npm run verify:telegram:gcal"
  "npm run verify:runtime:drift"
  "npm run security:check-orchestration"
)

status=0
{
  echo "[daily-check] start $(date -Iseconds)"
  for check in "${checks[@]}"; do
    echo
    echo "[daily-check] RUN $check"
    if eval "$check"; then
      echo "[daily-check] OK $check"
    else
      echo "[daily-check] FAIL $check"
      status=1
      break
    fi
  done
  echo
  if [[ "$status" -eq 0 ]]; then
    echo "[daily-check] PASS"
  else
    echo "[daily-check] FAILED"
  fi
} 2>&1 | tee "$LOG_FILE"

ln -sf "$(basename "$LOG_FILE")" "$LATEST_LINK"
exit "$status"
