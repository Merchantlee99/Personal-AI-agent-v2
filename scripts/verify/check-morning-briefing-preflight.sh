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
curl -sS http://127.0.0.1:8001/health >/tmp/nanoclaw_morning_health.json
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
