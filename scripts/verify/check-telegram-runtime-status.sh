#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
source scripts/runtime/load-env.sh

ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env.local}"
load_runtime_env "$ENV_FILE"

BOT_TOKEN="$(runtime_env_get TELEGRAM_BOT_TOKEN)"
if [[ -z "$BOT_TOKEN" ]]; then
  echo "[telegram-runtime] skip: TELEGRAM_BOT_TOKEN is empty"
  exit 0
fi

if ! docker ps --format '{{.Names}} {{.Status}}' | grep -q '^nanoclaw-telegram-poller '; then
  echo "[telegram-runtime] telegram-poller container is not running" >&2
  exit 1
fi

STATE_FILE="shared_data/shared_memory/telegram_poller_state.json"
if [[ -f "$STATE_FILE" ]]; then
  python3 - <<'PY' "$STATE_FILE"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
offset = payload.get("offset")
if offset is not None and not isinstance(offset, int):
    raise SystemExit("[telegram-runtime] invalid poller offset")
print(f"[telegram-runtime] poller offset={offset}")
PY
else
  echo "[telegram-runtime] WARN state file missing: ${STATE_FILE}"
fi

if [[ -s "shared_data/logs/telegram_poller_dead_letter.jsonl" ]]; then
  echo "[telegram-runtime] WARN dead-letter entries present"
else
  echo "[telegram-runtime] dead-letter clear"
fi

echo "[telegram-runtime] PASS"
