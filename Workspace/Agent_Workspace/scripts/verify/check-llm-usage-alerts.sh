#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

SINCE_WINDOW="${LLM_USAGE_SINCE:-24h}"
ALERT_THRESHOLD_429="${LLM_ALERT_429_THRESHOLD:-0}"
STRICT_MODE="${LLM_ALERT_STRICT:-false}"
METRICS_PATH="${LLM_USAGE_METRICS_PATH:-shared_data/logs/llm_usage_metrics.json}"

total_agent_calls=0
success_calls=0
server_errors=0
quota_429_hits=0

if [[ -f "$METRICS_PATH" ]]; then
  echo "[llm-usage] reading metrics store: $METRICS_PATH"
  read -r total_agent_calls success_calls server_errors quota_429_hits <<< "$(
    python3 - "$METRICS_PATH" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
daily = data.get("daily") or {}
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
entry = daily.get(today) or {}
total = int(entry.get("total", 0))
success = int(entry.get("success", 0))
transient = int(entry.get("transient_error", 0))
fatal = int(entry.get("fatal_error", 0))
quota = int(entry.get("quota_429", 0))
print(total, success, transient + fatal, quota)
PY
  )"
else
  echo "[llm-usage] metrics store not found, fallback to logs since ${SINCE_WINDOW}"
  logs="$(docker compose logs llm-proxy --since "$SINCE_WINDOW" 2>/dev/null || true)"
  total_agent_calls="$(printf "%s" "$logs" | rg -c 'POST /api/agent HTTP/1.1' || true)"
  success_calls="$(printf "%s" "$logs" | rg -c 'POST /api/agent HTTP/1.1" 200' || true)"
  server_errors="$(printf "%s" "$logs" | rg -c 'POST /api/agent HTTP/1.1" 5[0-9][0-9]' || true)"
  quota_429_hits="$(printf "%s" "$logs" | rg -c 'retryable_llm_error .*429|RESOURCE_EXHAUSTED|quota exceeded' || true)"
fi

total_agent_calls="${total_agent_calls:-0}"
success_calls="${success_calls:-0}"
server_errors="${server_errors:-0}"
quota_429_hits="${quota_429_hits:-0}"

echo "[llm-usage] total_agent_calls=$total_agent_calls"
echo "[llm-usage] success_calls=$success_calls"
echo "[llm-usage] server_errors=$server_errors"
echo "[llm-usage] quota_429_hits=$quota_429_hits"

if [[ "$quota_429_hits" -gt "$ALERT_THRESHOLD_429" ]]; then
  echo "[llm-usage] ALERT: quota 429 events exceeded threshold ($quota_429_hits > $ALERT_THRESHOLD_429)"
  if [[ "$STRICT_MODE" == "true" ]]; then
    exit 1
  fi
else
  echo "[llm-usage] OK: quota 429 events within threshold"
fi

echo "[llm-usage] PASS"
