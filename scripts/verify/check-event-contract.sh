#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
source "${REPO_ROOT}/scripts/runtime/compose-env.sh"
source "${REPO_ROOT}/scripts/runtime/load-env.sh"
load_runtime_env "${COMPOSE_ENV_FILE:-${REPO_ROOT}/.env.local}"

API_PORT="${API_PORT:-8001}"
BASE_URL="http://127.0.0.1:${API_PORT}"
require_schema="${ORCH_REQUIRE_SCHEMA_V1:-false}"

mkdir -p "${REPO_ROOT}/shared_data"/{inbox,outbox,archive,logs,verified_inbox,obsidian_vault,shared_memory,queue,workflows}
chmod -R a+rwX "${REPO_ROOT}/shared_data" || true

wait_for_proxy_health() {
  local retries="${1:-30}"
  local sleep_sec="${2:-1}"
  local container_id=""
  local status=""

  for _ in $(seq 1 "$retries"); do
    container_id="$(docker compose ps -q llm-proxy 2>/dev/null || true)"
    if [[ -z "$container_id" ]]; then
      sleep "$sleep_sec"
      continue
    fi
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
    if [[ "$status" == "healthy" || "$status" == "running" ]]; then
      return 0
    fi
    sleep "$sleep_sec"
  done

  echo "[event-contract] llm-proxy not ready (status=${status:-unknown} container_id=${container_id:-none})" >&2
  compose_cmd logs --tail=100 llm-proxy >&2 || true
  return 1
}

echo "[event-contract] ensure llm-proxy runtime"
compose_cmd up -d --build llm-proxy >/dev/null
wait_for_proxy_health 30 1

if docker ps --format '{{.Names}}' | grep -qx 'nanoclaw-llm-proxy'; then
  runtime_require_schema="$(docker exec nanoclaw-llm-proxy sh -lc "printenv ORCH_REQUIRE_SCHEMA_V1 2>/dev/null || true" | tr -d '\r')"
  require_schema="${runtime_require_schema:-false}"
fi
require_schema="$(printf '%s' "$require_schema" | tr '[:upper:]' '[:lower:]')"

run_case() {
  local name="$1"
  local payload="$2"
  local expected_status="$3"
  local output="/tmp/event_contract_${name}.json"
  local payload_file="/tmp/event_contract_${name}.payload.json"

  printf '%s' "$payload" >"$payload_file"
  local status
  status="$(bash "${REPO_ROOT}/scripts/runtime/internal-api-request.sh" POST "${BASE_URL}/api/orchestration/events" "$output" "$payload_file" || true)"

  if [[ "$status" != "$expected_status" ]]; then
    echo "[event-contract] ${name} unexpected status=${status} expected=${expected_status}" >&2
    cat "$output" >&2 || true
    exit 1
  fi

  cat "$output"
}

echo "[event-contract] case=v1_explicit"
run_case "v1_explicit" '{
  "schemaVersion": 1,
  "agentId": "hermes",
  "topicKey": "event-contract-v1",
  "title": "Event Contract V1 Explicit",
  "summary": "schemaVersion=1 contract validation",
  "priority": "normal",
  "confidence": 0.64,
  "tags": ["contract", "verification"],
  "sourceRefs": [{"title": "source", "url": "https://example.com/a"}]
}' "200" >/tmp/event_contract_v1_explicit.json

python3 - <<'PY'
import json
from pathlib import Path
payload = json.loads(Path('/tmp/event_contract_v1_explicit.json').read_text(encoding='utf-8'))
assert payload.get('ok') is True
assert payload.get('schemaVersion') == 1
assert payload.get('contractMode') in {'strict_v1', 'legacy_defaulted_v1'}
print('[event-contract] v1 explicit ok')
PY

echo "[event-contract] case=legacy_without_schema"
legacy_expected_status="200"
if [[ "$require_schema" == "1" || "$require_schema" == "true" || "$require_schema" == "yes" || "$require_schema" == "on" ]]; then
  legacy_expected_status="400"
fi

run_case "legacy" '{
  "agentId": "hermes",
  "topicKey": "event-contract-legacy",
  "title": "Event Contract Legacy",
  "summary": "legacy payload without schemaVersion",
  "priority": "normal",
  "confidence": 0.62,
  "tags": ["contract", "legacy"]
}' "$legacy_expected_status" >/tmp/event_contract_legacy.json

if [[ "$legacy_expected_status" == "200" ]]; then
  python3 - <<'PY'
import json
from pathlib import Path
payload = json.loads(Path('/tmp/event_contract_legacy.json').read_text(encoding='utf-8'))
assert payload.get('ok') is True
assert payload.get('schemaVersion') == 1
assert payload.get('contractMode') == 'legacy_defaulted_v1'
print('[event-contract] legacy compatibility ok')
PY
else
  python3 - <<'PY'
import json
from pathlib import Path
payload = json.loads(Path('/tmp/event_contract_legacy.json').read_text(encoding='utf-8'))
assert payload.get('error') == 'invalid_event_contract'
issues = payload.get('issues') or []
assert any('schemaVersion is required' in str(item) for item in issues)
print('[event-contract] strict mode legacy rejection ok')
PY
fi

echo "[event-contract] case=invalid_schema_version"
run_case "invalid_schema" '{
  "schemaVersion": 2,
  "agentId": "hermes",
  "topicKey": "event-contract-invalid",
  "title": "Event Contract Invalid",
  "summary": "invalid schema version",
  "priority": "normal",
  "confidence": 0.55
}' "400" >/tmp/event_contract_invalid_schema.json

python3 - <<'PY'
import json
from pathlib import Path
payload = json.loads(Path('/tmp/event_contract_invalid_schema.json').read_text(encoding='utf-8'))
assert payload.get('error') == 'invalid_event_contract'
issues = payload.get('issues') or []
assert any('unsupported schemaVersion' in str(item) for item in issues)
print('[event-contract] invalid schema rejected')
PY

echo "[event-contract] case=invalid_force_theme"
run_case "invalid_force_theme" '{
  "schemaVersion": 1,
  "agentId": "hermes",
  "topicKey": "event-contract-force-theme",
  "title": "Event Contract Force Theme",
  "summary": "invalid force theme value",
  "priority": "normal",
  "confidence": 0.55,
  "forceTheme": "nope"
}' "400" >/tmp/event_contract_invalid_force_theme.json

python3 - <<'PY'
import json
from pathlib import Path
payload = json.loads(Path('/tmp/event_contract_invalid_force_theme.json').read_text(encoding='utf-8'))
assert payload.get('error') == 'invalid_event_contract'
issues = payload.get('issues') or []
assert any('forceTheme must be one of' in str(item) for item in issues)
print('[event-contract] invalid forceTheme rejected')
PY

echo "[event-contract] PASS"
