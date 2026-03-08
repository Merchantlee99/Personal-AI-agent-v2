#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
source scripts/runtime/load-env.sh

ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env.local}"
load_runtime_env "$ENV_FILE"

fail_step() {
  local step="$1"
  local cause="$2"
  local next_action="$3"
  echo "[morning-preflight] FAIL step=${step}" >&2
  echo "[morning-preflight] cause=${cause}" >&2
  echo "[morning-preflight] next_action=${next_action}" >&2
  exit 1
}

run_step() {
  local step="$1"
  local cause="$2"
  local next_action="$3"
  shift 3
  echo "[morning-preflight] step=${step}"
  if ! "$@"; then
    fail_step "$step" "$cause" "$next_action"
  fi
}

echo "[morning-preflight] ensure runtime is up"
if ! bash scripts/runtime/compose.sh up -d llm-proxy telegram-poller nanoclaw-agent n8n >/dev/null; then
  fail_step "runtime_up" "docker compose up failed" "bash scripts/runtime/compose.sh logs llm-proxy --tail=120"
fi

echo "[morning-preflight] step=proxy_health"
HEALTH_STATUS="000"
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  HEALTH_STATUS="$(curl -sS -o /tmp/nanoclaw_morning_health.json -w '%{http_code}' http://127.0.0.1:8001/health || true)"
  if [[ "$HEALTH_STATUS" == "200" ]]; then
    break
  fi
  sleep 1
done
if [[ "$HEALTH_STATUS" != "200" ]]; then
  cat /tmp/nanoclaw_morning_health.json >&2 || true
  fail_step "proxy_health" "llm-proxy /health returned status=${HEALTH_STATUS}" "bash scripts/runtime/compose.sh logs llm-proxy --tail=120"
fi
if ! python3 - <<'PY'
import json
from pathlib import Path
payload = json.loads(Path("/tmp/nanoclaw_morning_health.json").read_text(encoding="utf-8"))
if payload.get("status") != "ok":
    raise SystemExit(1)
print("[morning-preflight] llm-proxy health ok")
PY
then
  fail_step "proxy_health" "llm-proxy reported non-ok payload" "bash scripts/runtime/compose.sh logs llm-proxy --tail=120"
fi

run_step \
  "hermes_schedule" \
  "Hermes daily briefing schedule or dispatch path failed" \
  "bash scripts/n8n/bootstrap-hermes-daily-briefing.sh && bash scripts/verify/check-hermes-schedule-minerva.sh" \
  bash scripts/verify/check-hermes-schedule-minerva.sh

run_step \
  "calendar_attach" \
  "morning calendar attach path failed" \
  "bash scripts/verify/check-morning-calendar-attach.sh && inspect Google Calendar token/status" \
  bash scripts/verify/check-morning-calendar-attach.sh

run_step \
  "telegram_text" \
  "Telegram Minerva text path failed" \
  "bash scripts/verify/check-telegram-minerva-chat.sh && inspect telegram-poller / llm-proxy logs" \
  bash scripts/verify/check-telegram-minerva-chat.sh

run_step \
  "runtime_drift" \
  "runtime drift detected between source and active runtime" \
  "bash scripts/verify/check-runtime-drift.sh && re-bootstrap drifted workflow or env" \
  bash scripts/verify/check-runtime-drift.sh

echo "[morning-preflight] PASS"
