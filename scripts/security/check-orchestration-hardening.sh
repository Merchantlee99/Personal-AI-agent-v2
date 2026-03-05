#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-.env.local}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[security-orch] env file missing: $ENV_FILE" >&2
  exit 1
fi

get_env() {
  local key="$1"
  local value
  value="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true)"
  printf '%s' "$value"
}

normalize_bool() {
  local raw
  raw="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | xargs)"
  case "$raw" in
    1|true|yes|on) echo "true" ;;
    0|false|no|off) echo "false" ;;
    *) echo "" ;;
  esac
}

FAIL=0

require_non_empty() {
  local key="$1"
  local value
  value="$(get_env "$key")"
  if [[ -z "$value" ]]; then
    echo "[security-orch] FAIL missing ${key}" >&2
    FAIL=1
  else
    echo "[security-orch] OK ${key}=set"
  fi
}

echo "[security-orch] checking orchestration/security baseline in ${ENV_FILE}"

GCAL_ENABLED="$(normalize_bool "$(get_env GOOGLE_CALENDAR_ENABLED)")"
GCAL_SCOPE="$(get_env GOOGLE_CALENDAR_OAUTH_SCOPES)"
if [[ "$GCAL_ENABLED" == "true" ]]; then
  require_non_empty GOOGLE_CALENDAR_OAUTH_CLIENT_ID
  require_non_empty GOOGLE_CALENDAR_OAUTH_CLIENT_SECRET
  if [[ "$GCAL_SCOPE" != "https://www.googleapis.com/auth/calendar.readonly" ]]; then
    echo "[security-orch] FAIL GOOGLE_CALENDAR_OAUTH_SCOPES must be readonly only" >&2
    FAIL=1
  else
    echo "[security-orch] OK GOOGLE_CALENDAR_OAUTH_SCOPES is readonly"
  fi
else
  echo "[security-orch] WARN GOOGLE_CALENDAR_ENABLED is not true"
fi

AUTO_CLIO="$(normalize_bool "$(get_env HERMES_AUTO_CLIO_SAVE)")"
if [[ "$AUTO_CLIO" != "true" ]]; then
  echo "[security-orch] FAIL HERMES_AUTO_CLIO_SAVE must be true for high-impact auto-save policy" >&2
  FAIL=1
else
  echo "[security-orch] OK HERMES_AUTO_CLIO_SAVE=true"
fi

AUTO_CLIO_IMPACT="$(get_env HERMES_AUTO_CLIO_SAVE_MIN_IMPACT)"
python3 - <<'PY' "$AUTO_CLIO_IMPACT" || FAIL=1
import sys
raw = sys.argv[1].strip()
try:
    value = float(raw)
except ValueError:
    raise SystemExit(1)
if value < 0 or value > 1:
    raise SystemExit(1)
print(f"[security-orch] OK HERMES_AUTO_CLIO_SAVE_MIN_IMPACT={value}")
PY

require_non_empty ORCHESTRATION_EVENT_URL

SEARCH_PROVIDER="$(printf '%s' "$(get_env SEARCH_PROVIDER)" | tr '[:upper:]' '[:lower:]' | xargs)"
if [[ -z "$SEARCH_PROVIDER" ]]; then
  SEARCH_PROVIDER="auto"
fi
if [[ "$SEARCH_PROVIDER" != "auto" && "$SEARCH_PROVIDER" != "mock" && "$SEARCH_PROVIDER" != "tavily" ]]; then
  echo "[security-orch] FAIL SEARCH_PROVIDER must be one of auto/mock/tavily" >&2
  FAIL=1
else
  echo "[security-orch] OK SEARCH_PROVIDER=${SEARCH_PROVIDER}"
fi
if [[ "$SEARCH_PROVIDER" == "tavily" ]]; then
  require_non_empty TAVILY_API_KEY
fi

HERMES_SEARCH_PROVIDER="$(printf '%s' "$(get_env HERMES_SEARCH_PROVIDER)" | tr '[:upper:]' '[:lower:]' | xargs)"
if [[ -z "$HERMES_SEARCH_PROVIDER" ]]; then
  HERMES_SEARCH_PROVIDER="auto"
fi
if [[ "$HERMES_SEARCH_PROVIDER" != "auto" && "$HERMES_SEARCH_PROVIDER" != "tavily" && "$HERMES_SEARCH_PROVIDER" != "hn" ]]; then
  echo "[security-orch] FAIL HERMES_SEARCH_PROVIDER must be one of auto/tavily/hn" >&2
  FAIL=1
else
  echo "[security-orch] OK HERMES_SEARCH_PROVIDER=${HERMES_SEARCH_PROVIDER}"
fi
if [[ "$HERMES_SEARCH_PROVIDER" == "tavily" ]]; then
  require_non_empty TAVILY_API_KEY
fi

echo "[security-orch] checking n8n prompt-injection filters"
python3 - <<'PY' || FAIL=1
import json
from pathlib import Path

checks = [
    {
        "file": Path("n8n/workflows/hermes-daily-briefing.json"),
        "nodes": [
            ("Normalize Input", ("INJECTION_PATTERNS", "isSafeUrl")),
            ("Collect P0 Signals", ("INJECTION_PATTERNS", "isSafeUrl", "TAVILY_API_KEY")),
            ("Collect P1 Signals", ("INJECTION_PATTERNS", "isSafeUrl", "TAVILY_API_KEY")),
            ("Collect P2 Signals", ("INJECTION_PATTERNS", "isSafeUrl", "TAVILY_API_KEY")),
        ],
    },
    {
        "file": Path("n8n/workflows/hermes-web-search-tavily.json"),
        "nodes": [
            ("Normalize Search Input", ("INJECTION_PATTERNS",)),
            ("Fetch Search Signals", ("INJECTION_PATTERNS", "isSafeUrl", "TAVILY_API_KEY")),
        ],
    },
]

for item in checks:
    wf_file = item["file"]
    if not wf_file.exists():
        raise SystemExit(f"[security-orch] FAIL missing workflow file: {wf_file}")
    workflow = json.loads(wf_file.read_text(encoding="utf-8"))
    code_by_name = {
        node.get("name"): str(node.get("parameters", {}).get("jsCode", ""))
        for node in workflow.get("nodes", [])
        if node.get("type") == "n8n-nodes-base.code"
    }
    for node_name, markers in item["nodes"]:
        code = code_by_name.get(node_name, "")
        if not code:
            raise SystemExit(f"[security-orch] FAIL missing code node '{node_name}' in {wf_file}")
        missing = [marker for marker in markers if marker not in code]
        if missing:
            raise SystemExit(
                f"[security-orch] FAIL node '{node_name}' in {wf_file} missing markers: {', '.join(missing)}"
            )

print("[security-orch] OK n8n workflow filters verified")
PY

NOTEBOOKLM_SYNC="$(normalize_bool "$(get_env NOTEBOOKLM_SYNC_ENABLED)")"
if [[ "$NOTEBOOKLM_SYNC" == "true" ]]; then
  require_non_empty NOTEBOOKLM_INGEST_WEBHOOK_URL
else
  echo "[security-orch] WARN NOTEBOOKLM_SYNC_ENABLED is not true"
fi

TELEGRAM_TOKEN="$(get_env TELEGRAM_BOT_TOKEN)"
if [[ -n "$TELEGRAM_TOKEN" ]]; then
  require_non_empty TELEGRAM_WEBHOOK_SECRET
  require_non_empty TELEGRAM_ALLOWED_USER_IDS
  require_non_empty TELEGRAM_ALLOWED_CHAT_IDS
  ACTIONS="$(get_env TELEGRAM_ALLOWED_CALLBACK_ACTIONS)"
  if [[ "$ACTIONS" != *"clio_save"* || "$ACTIONS" != *"hermes_deep_dive"* || "$ACTIONS" != *"minerva_insight"* ]]; then
    echo "[security-orch] FAIL TELEGRAM_ALLOWED_CALLBACK_ACTIONS must include clio_save/hermes_deep_dive/minerva_insight" >&2
    FAIL=1
  else
    echo "[security-orch] OK TELEGRAM_ALLOWED_CALLBACK_ACTIONS contains required actions"
  fi

  APPROVAL_QUEUE="$(normalize_bool "$(get_env TELEGRAM_APPROVAL_QUEUE_ENABLED)")"
  if [[ "$APPROVAL_QUEUE" == "false" ]]; then
    echo "[security-orch] WARN TELEGRAM_APPROVAL_QUEUE_ENABLED=false (high-risk inline actions will execute immediately)"
  else
    echo "[security-orch] OK TELEGRAM_APPROVAL_QUEUE_ENABLED=true"
  fi
  APPROVAL_TTL="$(get_env TELEGRAM_APPROVAL_TTL_SEC)"
  if [[ -z "$APPROVAL_TTL" ]]; then
    echo "[security-orch] WARN TELEGRAM_APPROVAL_TTL_SEC is not set (default 300s)"
  else
    python3 - <<'PY' "$APPROVAL_TTL" || FAIL=1
import sys
raw = sys.argv[1].strip()
try:
    value = int(raw)
except ValueError:
    raise SystemExit(1)
if value < 60:
    raise SystemExit(1)
print(f"[security-orch] OK TELEGRAM_APPROVAL_TTL_SEC={value}")
PY
  fi
else
  echo "[security-orch] WARN TELEGRAM_BOT_TOKEN is empty; telegram hardening checks skipped"
fi

if docker compose ps >/tmp/security_orch_compose_ps.txt 2>/dev/null; then
  if grep -q "nanoclaw-agent" /tmp/security_orch_compose_ps.txt; then
    docker inspect nanoclaw-agent --format '[security-orch] agent read_only={{.HostConfig.ReadonlyRootfs}} cap_drop={{json .HostConfig.CapDrop}} no_new_priv={{json .HostConfig.SecurityOpt}}' || true
    docker inspect nanoclaw-llm-proxy --format '[security-orch] proxy read_only={{.HostConfig.ReadonlyRootfs}} cap_drop={{json .HostConfig.CapDrop}} no_new_priv={{json .HostConfig.SecurityOpt}}' || true
    docker inspect nanoclaw-n8n --format '[security-orch] n8n read_only={{.HostConfig.ReadonlyRootfs}} cap_drop={{json .HostConfig.CapDrop}} no_new_priv={{json .HostConfig.SecurityOpt}}' || true
  fi
fi

if [[ "$FAIL" -ne 0 ]]; then
  echo "[security-orch] FAILED" >&2
  exit 1
fi

echo "[security-orch] PASS"
