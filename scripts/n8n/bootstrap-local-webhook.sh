#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
source scripts/runtime/compose-env.sh

WORKFLOW_FILE="${1:-n8n/workflows/nanoclaw-v2-smoke.json}"
WORKFLOW_NAME="${N8N_BOOTSTRAP_WORKFLOW_NAME:-NanoClaw v2 Smoke Webhook}"
WEBHOOK_PATH="${N8N_BOOTSTRAP_WEBHOOK_PATH:-nanoclaw-v2-smoke}"
FORCE_IMPORT="${N8N_BOOTSTRAP_FORCE_IMPORT:-false}"
ALLOW_DUPLICATE_IMPORT="${N8N_BOOTSTRAP_ALLOW_DUPLICATE_IMPORT:-false}"
PURGE_INACTIVE_DUPLICATES="${N8N_BOOTSTRAP_PURGE_INACTIVE_DUPLICATES:-true}"
purge_required=false

workflow_definition_matches() {
  local workflow_id="$1"
  local current_file
  current_file="$(mktemp)"
  docker exec nanoclaw-n8n n8n export:workflow --id="$workflow_id" --output=/tmp/bootstrap-current-workflow.json >/dev/null 2>&1
  docker exec nanoclaw-n8n cat /tmp/bootstrap-current-workflow.json >"$current_file"
  python3 - <<'PY' "$WORKFLOW_FILE" "$current_file"
import json
import sys
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
            if key in {"createdAt", "updatedAt", "id", "versionId", "active", "isArchived", "staticData", "meta", "pinData", "triggerCount", "tags", "webhookId"}:
                continue
            filtered[key] = normalize(value)
        return filtered
    return obj

local = normalize(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
current = normalize(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
if local == current:
    print("match")
    raise SystemExit(0)
print("drift")
raise SystemExit(1)
PY
}

get_workflow_ids() {
  docker exec nanoclaw-n8n n8n list:workflow 2>/dev/null \
    | awk -F'|' -v target="$WORKFLOW_NAME" '$2 == target {print $1}'
}

is_workflow_active() {
  local workflow_id="$1"
  docker exec nanoclaw-n8n n8n list:workflow --active=true 2>/dev/null \
    | awk -F'|' -v target="$workflow_id" '$1 == target {found=1} END{exit found?0:1}'
}

wait_for_n8n_cli() {
  for i in $(seq 1 30); do
    if docker exec nanoclaw-n8n n8n list:workflow >/dev/null 2>&1; then
      return 0
    fi
    echo "[bootstrap] waiting for n8n CLI ready ($i/30)"
    sleep 2
  done
  echo "[bootstrap] n8n CLI not ready after retries" >&2
  return 1
}

wait_for_n8n_http() {
  for i in $(seq 1 60); do
    status="$(curl -s -o /tmp/n8n_bootstrap_http.txt -w '%{http_code}' http://localhost:5678/ || true)"
    if [[ "$status" == "200" || "$status" == "302" || "$status" == "401" ]]; then
      return 0
    fi
    echo "[bootstrap] waiting for n8n HTTP ready ($i/60) status=$status"
    sleep 2
  done
  echo "[bootstrap] n8n HTTP not ready after retries" >&2
  [[ -f /tmp/n8n_bootstrap_http.txt ]] && cat /tmp/n8n_bootstrap_http.txt >&2 || true
  return 1
}

if [[ ! -f "$WORKFLOW_FILE" ]]; then
  echo "[bootstrap] workflow file not found: $WORKFLOW_FILE" >&2
  exit 1
fi

echo "[bootstrap] ensuring n8n service is running"
compose_cmd up -d n8n >/dev/null

if ! compose_cmd ps -a | grep -E "nanoclaw-n8n\s" | grep -q "Up"; then
  echo "[bootstrap] n8n is not running. check 'bash scripts/runtime/compose.sh logs n8n'" >&2
  exit 1
fi
wait_for_n8n_cli

mkdir -p shared_data/workflows
TARGET_FILE="shared_data/workflows/$(basename "$WORKFLOW_FILE")"
cp "$WORKFLOW_FILE" "$TARGET_FILE"

before_ids="$(get_workflow_ids || true)"
workflow_count_before="$(printf '%s\n' "$before_ids" | awk 'NF{count++} END{print count+0}')"
existing_workflow_id="$(printf '%s\n' "$before_ids" | awk 'NF{id=$1} END{print id}')"

if [[ "$workflow_count_before" -gt 1 && -n "$existing_workflow_id" ]]; then
  echo "[bootstrap] pre-clean duplicate workflows count=$workflow_count_before keep=$existing_workflow_id"
  N8N_WORKFLOW_NAME="$WORKFLOW_NAME" \
  N8N_KEEP_WORKFLOW_ID="$existing_workflow_id" \
  bash scripts/n8n/cleanup-duplicate-workflows.sh >/tmp/n8n_precleanup.log 2>&1 || {
    cat /tmp/n8n_precleanup.log >&2
    exit 1
  }
  cat /tmp/n8n_precleanup.log
  before_ids="$existing_workflow_id"
  purge_required=true
fi

workflow_id="$existing_workflow_id"
current_is_active=false
if [[ -n "$workflow_id" ]] && is_workflow_active "$workflow_id"; then
  current_is_active=true
fi

if [[ -n "$existing_workflow_id" && "$FORCE_IMPORT" != "true" ]]; then
  if workflow_definition_matches "$existing_workflow_id" >/tmp/n8n_workflow_compare.log 2>&1; then
    echo "[bootstrap] existing workflow matches file id=$existing_workflow_id"
  else
    echo "[bootstrap] workflow drift detected for id=$existing_workflow_id; refreshing definition"
    cat /tmp/n8n_workflow_compare.log
    FORCE_IMPORT="true"
    ALLOW_DUPLICATE_IMPORT="true"
  fi
fi

if [[ -n "$existing_workflow_id" && "$FORCE_IMPORT" == "true" && "$ALLOW_DUPLICATE_IMPORT" != "true" ]]; then
  echo "[bootstrap] force import requested for existing workflow id=$existing_workflow_id"
  echo "[bootstrap] enabling duplicate-safe import; old definitions will be deactivated after import"
  ALLOW_DUPLICATE_IMPORT="true"
fi

if [[ -n "$workflow_id" && "$FORCE_IMPORT" != "true" && "$current_is_active" == "true" && "$purge_required" != "true" ]]; then
  echo "[bootstrap] workflow already active with matching definition; no changes required"
  exit 0
fi

restart_required=false

if [[ -n "$workflow_id" && ! ("$FORCE_IMPORT" == "true" && "$ALLOW_DUPLICATE_IMPORT" == "true") ]]; then
  echo "[bootstrap] reusing existing workflow id=$workflow_id"
  after_ids="$before_ids"
else
  echo "[bootstrap] importing workflow from $TARGET_FILE"
  if ! docker exec nanoclaw-n8n n8n import:workflow --input="/data/$TARGET_FILE" >/tmp/n8n_import.log 2>&1; then
    cat /tmp/n8n_import.log >&2
    exit 1
  fi
  after_ids="$(get_workflow_ids || true)"
  workflow_id="$(
    comm -13 \
      <(printf '%s\n' "$before_ids" | awk 'NF' | sort) \
      <(printf '%s\n' "$after_ids" | awk 'NF' | sort) \
      | awk 'NF {print; exit}'
  )"
  if [[ -z "$workflow_id" ]]; then
    # Fallback for cases where import reused an ID unexpectedly.
    workflow_id="$(printf '%s\n' "$after_ids" | awk 'NF{id=$1} END{print id}')"
  fi
  restart_required=true
fi

if [[ -z "$workflow_id" ]]; then
  echo "[bootstrap] failed to find workflow id for name: $WORKFLOW_NAME" >&2
  docker exec nanoclaw-n8n n8n list:workflow 2>/dev/null || true
  exit 1
fi

workflow_ids="$(get_workflow_ids)"
workflow_count="$(printf '%s\n' "$workflow_ids" | awk 'NF{count++} END{print count+0}')"
if [[ "$workflow_count" -gt 1 ]]; then
  echo "[bootstrap] found $workflow_count workflows with same name. keeping id=$workflow_id active."
  purge_required=true
  while IFS= read -r id; do
    if [[ -n "$id" && "$id" != "$workflow_id" ]]; then
      docker exec nanoclaw-n8n n8n update:workflow --id="$id" --active=false >/dev/null 2>&1 || true
    fi
  done <<< "$workflow_ids"
fi

echo "[bootstrap] activating workflow id=$workflow_id"
docker exec nanoclaw-n8n n8n update:workflow --id="$workflow_id" --active=true >/tmp/n8n_activate.log 2>&1 || {
  cat /tmp/n8n_activate.log >&2
  exit 1
}

if [[ "$current_is_active" != "true" || "$restart_required" == "true" ]]; then
  restart_required=true
fi

if [[ "$restart_required" == "true" ]]; then
  echo "[bootstrap] restarting n8n for activation"
  compose_cmd restart n8n >/dev/null
else
  echo "[bootstrap] activation already current; skipping n8n restart"
fi

if [[ "$restart_required" == "true" ]]; then
  wait_for_n8n_cli
  wait_for_n8n_http
fi

if [[ "$purge_required" == "true" && "$PURGE_INACTIVE_DUPLICATES" == "true" ]]; then
  echo "[bootstrap] purge inactive duplicates for $WORKFLOW_NAME"
  N8N_WORKFLOW_NAME="$WORKFLOW_NAME" \
  N8N_KEEP_WORKFLOW_ID="$workflow_id" \
  bash scripts/n8n/purge-inactive-duplicate-workflows.sh >/tmp/n8n_purge.log 2>&1 || {
    cat /tmp/n8n_purge.log >&2
    exit 1
  }
  cat /tmp/n8n_purge.log
fi

echo "[bootstrap] waiting for webhook to become available"
rm -f /tmp/n8n_bootstrap_webhook.json
for i in $(seq 1 120); do
  status="$(curl -s -o /tmp/n8n_bootstrap_webhook.json -w '%{http_code}' \
    -X POST "http://localhost:5678/webhook/$WEBHOOK_PATH" \
    -H 'content-type: application/json' \
    -d '{"ping":"pong","source":"bootstrap-check"}' || true)"
  if [[ "$status" == "200" ]]; then
    echo "[bootstrap] webhook is ready (200)"
    cat /tmp/n8n_bootstrap_webhook.json
    exit 0
  fi
  sleep 2
  echo "[bootstrap] retry $i -> status=$status"
done

echo "[bootstrap] webhook validation failed"
if [[ -f /tmp/n8n_bootstrap_webhook.json ]]; then
  cat /tmp/n8n_bootstrap_webhook.json
fi
exit 1
