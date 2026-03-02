#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

WORKFLOW_NAME="${N8N_WORKFLOW_NAME:-NanoClaw v2 Smoke Webhook}"
KEEP_WORKFLOW_ID="${N8N_KEEP_WORKFLOW_ID:-}"

echo "[cleanup] ensure n8n up"
docker compose up -d n8n >/dev/null

ids="$(docker exec nanoclaw-n8n n8n list:workflow 2>/dev/null | awk -F'|' -v target="$WORKFLOW_NAME" '$2 == target {print $1}')"
count="$(printf '%s\n' "$ids" | awk 'NF{c++} END{print c+0}')"

if [[ "$count" -eq 0 ]]; then
  echo "[cleanup] no workflow found by name: $WORKFLOW_NAME"
  exit 0
fi

if [[ -z "$KEEP_WORKFLOW_ID" ]]; then
  KEEP_WORKFLOW_ID="$(printf '%s\n' "$ids" | awk 'NF{id=$1} END{print id}')"
fi

echo "[cleanup] total=$count keep=$KEEP_WORKFLOW_ID"
while IFS= read -r id; do
  [[ -z "$id" ]] && continue
  if [[ "$id" == "$KEEP_WORKFLOW_ID" ]]; then
    docker exec nanoclaw-n8n n8n update:workflow --id="$id" --active=true >/dev/null 2>&1 || true
    echo "[cleanup] keep active: $id"
  else
    docker exec nanoclaw-n8n n8n update:workflow --id="$id" --active=false >/dev/null 2>&1 || true
    echo "[cleanup] deactivated duplicate: $id"
  fi
done <<< "$ids"

echo "[cleanup] restarting n8n"
docker compose restart n8n >/dev/null

echo "[cleanup] done"
