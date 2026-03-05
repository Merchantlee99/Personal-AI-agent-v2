#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
if [[ -f "${REPO_ROOT}/.env.local" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${REPO_ROOT}/.env.local"
  set +a
fi

FRONTEND_PORT="${FRONTEND_PORT:-3000}"
BASE_URL="http://127.0.0.1:${FRONTEND_PORT}"
require_schema="${ORCH_REQUIRE_SCHEMA_V1:-false}"
require_schema="$(printf '%s' "$require_schema" | tr '[:upper:]' '[:lower:]')"

run_case() {
  local name="$1"
  local payload="$2"
  local expected_status="$3"
  local output="/tmp/event_contract_${name}.json"

  local status
  status="$(
    curl -sS -o "$output" -w '%{http_code}' \
      -X POST "${BASE_URL}/api/orchestration/events" \
      -H 'content-type: application/json' \
      -d "$payload" || true
  )"

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

echo "[event-contract] PASS"
