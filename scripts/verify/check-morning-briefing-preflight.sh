#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
source scripts/runtime/load-env.sh

ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env.local}"
load_runtime_env "$ENV_FILE"

echo "[morning-preflight] ensure runtime is up"
bash scripts/runtime/compose.sh up -d llm-proxy telegram-poller nanoclaw-agent n8n >/dev/null

echo "[morning-preflight] check proxy health"
HEALTH_STATUS="000"
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  HEALTH_STATUS="$(curl -sS -o /tmp/nanoclaw_morning_health.json -w '%{http_code}' http://127.0.0.1:8001/health || true)"
  if [[ "$HEALTH_STATUS" == "200" ]]; then
    break
  fi
  sleep 1
done
if [[ "$HEALTH_STATUS" != "200" ]]; then
  echo "[morning-preflight] llm-proxy healthcheck failed status=$HEALTH_STATUS" >&2
  cat /tmp/nanoclaw_morning_health.json >&2 || true
  exit 1
fi
python3 - <<'PY'
import json
from pathlib import Path
payload = json.loads(Path("/tmp/nanoclaw_morning_health.json").read_text(encoding="utf-8"))
if payload.get("status") != "ok":
    raise SystemExit("[morning-preflight] llm-proxy health not ok")
print("[morning-preflight] llm-proxy health ok")
PY

echo "[morning-preflight] verify schedule + dispatch path"
bash scripts/verify/check-hermes-schedule-minerva.sh

echo "[morning-preflight] verify calendar attach path"
bash scripts/verify/check-morning-calendar-attach.sh

echo "[morning-preflight] verify telegram text path"
bash scripts/verify/check-telegram-minerva-chat.sh

echo "[morning-preflight] verify runtime drift"
bash scripts/verify/check-runtime-drift.sh

echo "[morning-preflight] PASS"
