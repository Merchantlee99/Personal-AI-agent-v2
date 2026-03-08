#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
source scripts/runtime/compose-env.sh

ENV_FILE="${ENV_FILE:-.env.local}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[runtime-drift] env file missing: $ENV_FILE" >&2
  exit 1
fi

get_env() {
  local key="$1"
  local value
  value="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true)"
  printf '%s' "$value"
}

container_env() {
  local container="$1"
  local key="$2"
  docker exec "$container" sh -lc "printenv '$key' 2>/dev/null || true" 2>/dev/null | tr -d '\r'
}

compare_env() {
  local container="$1"
  local key="$2"
  local default_value="${3:-}"
  local expected
  expected="$(get_env "$key")"
  if [[ -z "$expected" ]]; then
    expected="$default_value"
  fi
  local actual
  actual="$(container_env "$container" "$key")"
  if [[ "$actual" != "$expected" ]]; then
    echo "[runtime-drift] FAIL ${container} ${key} drift detected" >&2
    return 1
  fi
  echo "[runtime-drift] OK ${container} ${key}"
}

wait_for_n8n_cli() {
  for _ in $(seq 1 30); do
    if docker exec nanoclaw-n8n n8n list:workflow >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "[runtime-drift] FAIL n8n CLI not ready" >&2
  return 1
}

echo "[runtime-drift] ensure llm-proxy + n8n are running"
compose_cmd up -d llm-proxy n8n >/dev/null
wait_for_n8n_cli

compare_env nanoclaw-llm-proxy MINERVA_BRIEFING_TIMEZONE "Asia/Seoul"
compare_env nanoclaw-llm-proxy HERMES_AUTO_CLIO_SAVE "true"
compare_env nanoclaw-llm-proxy HERMES_AUTO_CLIO_SAVE_MIN_IMPACT "0.75"
compare_env nanoclaw-llm-proxy TELEGRAM_APPROVAL_TTL_SEC "300"
compare_env nanoclaw-llm-proxy TELEGRAM_APPROVAL_REQUIRED_STEPS "2"
compare_env nanoclaw-llm-proxy GOOGLE_CALENDAR_ATTACH_TO_MORNING_BRIEFING "true"
compare_env nanoclaw-llm-proxy SEARCH_PROVIDER "auto"
compare_env nanoclaw-llm-proxy TAVILY_API_ALLOWED_HOSTS "api.tavily.com"

compare_env nanoclaw-n8n ORCHESTRATION_EVENT_URL "http://llm-proxy:8000/api/orchestration/events"
compare_env nanoclaw-n8n N8N_DEFAULT_TIMEZONE "Asia/Seoul"
compare_env nanoclaw-n8n HERMES_SEARCH_PROVIDER "auto"
compare_env nanoclaw-n8n SEARCH_PROVIDER "auto"
compare_env nanoclaw-n8n TAVILY_API_BASE "https://api.tavily.com"
compare_env nanoclaw-n8n TAVILY_API_ALLOWED_HOSTS "api.tavily.com"
compare_env nanoclaw-n8n TAVILY_SEARCH_DEPTH "basic"

echo "[runtime-drift] exporting active workflows"
REMOTE_EXPORT_DIR="/tmp/runtime-drift-workflows"
LOCAL_EXPORT_DIR="$(mktemp -d /tmp/runtime-drift-workflows.XXXXXX)"
trap 'rm -rf "$LOCAL_EXPORT_DIR"' EXIT
docker exec nanoclaw-n8n sh -lc "rm -rf ${REMOTE_EXPORT_DIR} && mkdir -p ${REMOTE_EXPORT_DIR} && n8n export:workflow --backup --output=${REMOTE_EXPORT_DIR} >/dev/null && find ${REMOTE_EXPORT_DIR} -maxdepth 1 -type f -name '*.json' | sed 's#.*/##'" > "${LOCAL_EXPORT_DIR}/files.txt"
mkdir -p "${LOCAL_EXPORT_DIR}/exported"
while IFS= read -r workflow_file; do
  [[ -z "$workflow_file" ]] && continue
  docker exec nanoclaw-n8n cat "${REMOTE_EXPORT_DIR}/${workflow_file}" > "${LOCAL_EXPORT_DIR}/exported/${workflow_file}"
done < "${LOCAL_EXPORT_DIR}/files.txt"

python3 - <<'PY' "${LOCAL_EXPORT_DIR}/exported"
import json
from pathlib import Path


def normalize(obj):
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and "nodes" in obj[0]:
            return normalize(obj[0])
        return [normalize(item) for item in obj]
    if isinstance(obj, dict):
        if "nodes" in obj and "connections" in obj:
            return {
                "name": normalize(obj.get("name")),
                "nodes": normalize(obj.get("nodes")),
                "connections": normalize(obj.get("connections")),
                "settings": normalize(obj.get("settings", {})),
            }
        filtered = {}
        for key, value in obj.items():
            if key in {
                "createdAt",
                "updatedAt",
                "id",
                "versionId",
                "active",
                "isArchived",
                "staticData",
                "meta",
                "pinData",
                "triggerCount",
                "tags",
                "webhookId",
            }:
                continue
            filtered[key] = normalize(value)
        return filtered
    return obj


export_dir = Path(__import__("sys").argv[1])
exported = []
for path in sorted(export_dir.glob("*.json")):
    exported.append(json.loads(path.read_text(encoding="utf-8")))
if not exported:
    raise SystemExit("[runtime-drift] FAIL no exported workflows found")

workflow_map = {
    "NanoClaw v2 Smoke Webhook": Path("n8n/workflows/nanoclaw-v2-smoke.json"),
    "Hermes Daily Briefing Workflow": Path("n8n/workflows/hermes-daily-briefing.json"),
    "Hermes Web Search Workflow": Path("n8n/workflows/hermes-web-search-tavily.json"),
}

for workflow_name, local_path in workflow_map.items():
    matches = [item for item in exported if item.get("name") == workflow_name]
    active = [item for item in matches if item.get("active") is True]
    if len(active) != 1:
        raise SystemExit(
            f"[runtime-drift] FAIL {workflow_name!r} expected exactly one active workflow, got {len(active)}"
        )
    local = normalize(json.loads(local_path.read_text(encoding="utf-8")))
    current = normalize(active[0])
    if local != current:
        raise SystemExit(f"[runtime-drift] FAIL workflow drift detected for {workflow_name}")
    print(f"[runtime-drift] OK workflow {workflow_name}")
PY

echo "[runtime-drift] PASS"
