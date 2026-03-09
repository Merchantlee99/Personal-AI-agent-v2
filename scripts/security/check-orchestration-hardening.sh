#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
source scripts/runtime/compose-env.sh
source scripts/runtime/load-env.sh

ENV_FILE="${ENV_FILE:-.env.local}"
load_runtime_env "$ENV_FILE"

get_env() {
  local key="$1"
  runtime_env_get "$key"
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
GCAL_REDIRECT_URI="$(get_env GOOGLE_CALENDAR_OAUTH_REDIRECT_URI)"
if [[ "$GCAL_ENABLED" == "true" ]]; then
  require_non_empty GOOGLE_CALENDAR_OAUTH_CLIENT_ID
  require_non_empty GOOGLE_CALENDAR_OAUTH_CLIENT_SECRET
  if [[ "$GCAL_SCOPE" != "https://www.googleapis.com/auth/calendar.readonly" ]]; then
    echo "[security-orch] FAIL GOOGLE_CALENDAR_OAUTH_SCOPES must be readonly only" >&2
    FAIL=1
  else
    echo "[security-orch] OK GOOGLE_CALENDAR_OAUTH_SCOPES is readonly"
  fi
  if [[ "$GCAL_REDIRECT_URI" != "http://127.0.0.1:8001/api/integrations/google-calendar/oauth/callback" ]]; then
    echo "[security-orch] FAIL GOOGLE_CALENDAR_OAUTH_REDIRECT_URI must target llm-proxy callback (:8001)" >&2
    FAIL=1
  else
    echo "[security-orch] OK GOOGLE_CALENDAR_OAUTH_REDIRECT_URI targets llm-proxy"
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
require_non_empty INTERNAL_API_TOKEN
require_non_empty INTERNAL_SIGNING_SECRET

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

TAVILY_ALLOWED_HOSTS="$(get_env TAVILY_API_ALLOWED_HOSTS)"
if [[ -z "$TAVILY_ALLOWED_HOSTS" ]]; then
  echo "[security-orch] WARN TAVILY_API_ALLOWED_HOSTS is empty (default api.tavily.com will be used at runtime)"
else
  echo "[security-orch] OK TAVILY_API_ALLOWED_HOSTS=${TAVILY_ALLOWED_HOSTS}"
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
            ("Collect Tier Signals", ("INJECTION_PATTERNS", "isSafeUrl", "TAVILY_API_KEY", "allowedTavilyHosts")),
            ("Prepare P0 Config", ("query_base", "tier_domains", "heartbeat_url")),
            ("Prepare P1 Config", ("query_base", "tier_domains", "heartbeat_url")),
            ("Prepare P2 Config", ("query_base", "tier_domains", "heartbeat_url")),
            ("Build API Response", ("INTERNAL_API_TOKEN", "INTERNAL_SIGNING_SECRET", "createHmac", "x-internal-token")),
        ],
    },
    {
        "file": Path("n8n/workflows/hermes-web-search-tavily.json"),
        "nodes": [
            ("Normalize Search Input", ("INJECTION_PATTERNS",)),
            ("Fetch Search Signals", ("INJECTION_PATTERNS", "isSafeUrl", "TAVILY_API_KEY", "allowedTavilyHosts")),
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

if runtime_env_has_value TELEGRAM_BOT_TOKEN; then
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

container_env_is_set() {
  local container="$1"
  local key="$2"
  local value
  value="$(docker exec "$container" sh -lc "printenv '$key' 2>/dev/null || true" 2>/dev/null || true)"
  [[ -n "$value" ]]
}

container_env_value() {
  local container="$1"
  local key="$2"
  docker exec "$container" sh -lc "printenv '$key' 2>/dev/null || true" 2>/dev/null || true
}

assert_container_env_present() {
  local container="$1"
  local key="$2"
  if container_env_is_set "$container" "$key"; then
    echo "[security-orch] OK ${container} has ${key}"
  else
    echo "[security-orch] FAIL ${container} missing ${key}" >&2
    FAIL=1
  fi
}

assert_container_env_empty() {
  local container="$1"
  local key="$2"
  if container_env_is_set "$container" "$key"; then
    echo "[security-orch] FAIL ${container} should not carry ${key}" >&2
    FAIL=1
  else
    echo "[security-orch] OK ${container} does not carry ${key}"
  fi
}

if compose_cmd ps >/tmp/security_orch_compose_ps.txt 2>/dev/null; then
  if grep -q "nanoclaw-agent" /tmp/security_orch_compose_ps.txt; then
    docker inspect nanoclaw-agent --format '[security-orch] agent read_only={{.HostConfig.ReadonlyRootfs}} cap_drop={{json .HostConfig.CapDrop}} no_new_priv={{json .HostConfig.SecurityOpt}}' || true
    docker inspect nanoclaw-llm-proxy --format '[security-orch] proxy read_only={{.HostConfig.ReadonlyRootfs}} cap_drop={{json .HostConfig.CapDrop}} no_new_priv={{json .HostConfig.SecurityOpt}}' || true
    docker inspect nanoclaw-n8n --format '[security-orch] n8n read_only={{.HostConfig.ReadonlyRootfs}} cap_drop={{json .HostConfig.CapDrop}} no_new_priv={{json .HostConfig.SecurityOpt}}' || true
    docker inspect nanoclaw-telegram-poller --format '[security-orch] poller read_only={{.HostConfig.ReadonlyRootfs}} cap_drop={{json .HostConfig.CapDrop}} no_new_priv={{json .HostConfig.SecurityOpt}}' || true

    echo "[security-orch] checking service-specific secret minimization"
    assert_container_env_present nanoclaw-llm-proxy INTERNAL_API_TOKEN
    assert_container_env_present nanoclaw-llm-proxy TELEGRAM_BOT_TOKEN
    assert_container_env_empty nanoclaw-llm-proxy N8N_BASIC_AUTH_PASSWORD

    assert_container_env_present nanoclaw-telegram-poller TELEGRAM_BOT_TOKEN
    assert_container_env_empty nanoclaw-telegram-poller ANTHROPIC_API_KEY
    assert_container_env_empty nanoclaw-telegram-poller GOOGLE_CALENDAR_OAUTH_CLIENT_SECRET
    assert_container_env_empty nanoclaw-telegram-poller TAVILY_API_KEY

    assert_container_env_empty nanoclaw-agent TELEGRAM_BOT_TOKEN
    assert_container_env_empty nanoclaw-agent ANTHROPIC_API_KEY
    assert_container_env_empty nanoclaw-agent TAVILY_API_KEY

    assert_container_env_present nanoclaw-n8n ORCHESTRATION_EVENT_URL
    assert_container_env_present nanoclaw-n8n INTERNAL_API_TOKEN
    assert_container_env_present nanoclaw-n8n INTERNAL_SIGNING_SECRET
    assert_container_env_present nanoclaw-n8n NODE_FUNCTION_ALLOW_BUILTIN
    NODE_BUILTINS="$(container_env_value nanoclaw-n8n NODE_FUNCTION_ALLOW_BUILTIN)"
    if [[ "$NODE_BUILTINS" != *"crypto"* ]]; then
      echo "[security-orch] FAIL nanoclaw-n8n NODE_FUNCTION_ALLOW_BUILTIN must include crypto" >&2
      FAIL=1
    else
      echo "[security-orch] OK nanoclaw-n8n NODE_FUNCTION_ALLOW_BUILTIN includes crypto"
    fi
    assert_container_env_present nanoclaw-n8n TAVILY_API_KEY
    assert_container_env_empty nanoclaw-n8n TELEGRAM_BOT_TOKEN
    assert_container_env_empty nanoclaw-n8n GOOGLE_CALENDAR_OAUTH_CLIENT_SECRET
    assert_container_env_empty nanoclaw-n8n ANTHROPIC_API_KEY
  fi
fi

if [[ "$FAIL" -ne 0 ]]; then
  echo "[security-orch] FAILED" >&2
  exit 1
fi

echo "[security-orch] PASS"
