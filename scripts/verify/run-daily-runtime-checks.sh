#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

FRONTEND_PORT="${FRONTEND_PORT:-3000}"
LOG_DIR="${DAILY_VERIFY_LOG_DIR:-shared_data/logs}"
RUN_ID="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="${LOG_DIR}/daily-verify-${RUN_ID}.log"

mkdir -p "$LOG_DIR"
touch "$LOG_FILE"

echo "[verify-daily] started run_id=${RUN_ID}" | tee -a "$LOG_FILE"
echo "[verify-daily] frontend_port=${FRONTEND_PORT}" | tee -a "$LOG_FILE"

run_step() {
  local name="$1"
  local cmd="$2"
  echo "[verify-daily] >>> ${name}" | tee -a "$LOG_FILE"
  if /bin/bash -lc "$cmd" >>"$LOG_FILE" 2>&1; then
    echo "[verify-daily] OK ${name}" | tee -a "$LOG_FILE"
    return 0
  fi
  echo "[verify-daily] FAIL ${name}" | tee -a "$LOG_FILE"
  return 1
}

FAIL_COUNT=0

run_step "event-contract" "cd '${ROOT_DIR}' && npm run verify:event-contract" || FAIL_COUNT=$((FAIL_COUNT + 1))
run_step "orchestration" "cd '${ROOT_DIR}' && FRONTEND_PORT='${FRONTEND_PORT}' npm run verify:orchestration" || FAIL_COUNT=$((FAIL_COUNT + 1))
run_step "hermes-schedule" "cd '${ROOT_DIR}' && FRONTEND_PORT='${FRONTEND_PORT}' npm run verify:hermes:schedule" || FAIL_COUNT=$((FAIL_COUNT + 1))
run_step "memory-md" "cd '${ROOT_DIR}' && FRONTEND_PORT='${FRONTEND_PORT}' npm run verify:memory" || FAIL_COUNT=$((FAIL_COUNT + 1))

echo "[verify-daily] log_file=${LOG_FILE}" | tee -a "$LOG_FILE"
if [[ "$FAIL_COUNT" -gt 0 ]]; then
  echo "[verify-daily] FAILED count=${FAIL_COUNT}" | tee -a "$LOG_FILE"
  exit 1
fi

echo "[verify-daily] PASS" | tee -a "$LOG_FILE"
