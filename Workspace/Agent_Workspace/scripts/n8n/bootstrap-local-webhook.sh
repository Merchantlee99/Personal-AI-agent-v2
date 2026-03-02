#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

WORKFLOW_FILE="${1:-n8n/workflows/nanoclaw-v2-smoke.json}"
WORKFLOW_NAME="${N8N_BOOTSTRAP_WORKFLOW_NAME:-NanoClaw v2 Smoke Webhook}"
WEBHOOK_PATH="${N8N_BOOTSTRAP_WEBHOOK_PATH:-nanoclaw-v2-smoke}"
FORCE_IMPORT="${N8N_BOOTSTRAP_FORCE_IMPORT:-false}"

get_workflow_ids() {
  docker exec nanoclaw-n8n n8n list:workflow 2>/dev/null \
    | awk -F'|' -v target="$WORKFLOW_NAME" '$2 == target {print $1}'
}

wait_for_n8n_cli() {
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if docker exec nanoclaw-n8n n8n list:workflow >/dev/null 2>&1; then
      return 0
    fi
    echo "[bootstrap] waiting for n8n CLI ready ($i/10)"
    sleep 2
  done
  echo "[bootstrap] n8n CLI not ready after retries" >&2
  return 1
}

if [[ ! -f "$WORKFLOW_FILE" ]]; then
  echo "[bootstrap] workflow file not found: $WORKFLOW_FILE" >&2
  exit 1
fi

echo "[bootstrap] ensuring n8n service is running"
docker compose up -d n8n >/dev/null

if ! docker compose ps -a | grep -E "nanoclaw-n8n\s" | grep -q "Up"; then
  echo "[bootstrap] n8n is not running. check 'docker compose logs n8n'" >&2
  exit 1
fi
wait_for_n8n_cli

mkdir -p shared_data/workflows
TARGET_FILE="shared_data/workflows/$(basename "$WORKFLOW_FILE")"
cp "$WORKFLOW_FILE" "$TARGET_FILE"

before_ids="$(get_workflow_ids || true)"
workflow_id=""
if [[ "$FORCE_IMPORT" != "true" ]]; then
  workflow_id="$(printf '%s\n' "$before_ids" | awk 'NF{id=$1} END{print id}')"
fi

if [[ -n "$workflow_id" ]]; then
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

echo "[bootstrap] restarting n8n for activation"
docker compose restart n8n >/dev/null

echo "[bootstrap] waiting for webhook to become available"
for i in 1 2 3 4 5; do
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
