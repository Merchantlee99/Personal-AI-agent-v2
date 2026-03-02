#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="shared_data/workflows/backups/n8n-reset-${TIMESTAMP}"
mkdir -p "$BACKUP_DIR"

echo "[n8n-reset] ensure n8n up for backup"
docker compose up -d n8n >/dev/null

echo "[n8n-reset] backup n8n data -> $BACKUP_DIR"
docker cp nanoclaw-n8n:/home/node/.n8n "$BACKUP_DIR/n8n_data"

echo "[n8n-reset] stop n8n"
docker compose stop n8n >/dev/null
docker compose rm -f n8n >/dev/null

volume_name="$(
  docker volume ls --format '{{.Name}}' \
    | awk '/_n8n_data$/ {print; exit}'
)"
if [[ -z "$volume_name" ]]; then
  echo "[n8n-reset] volume not found: *_n8n_data" >&2
  exit 1
fi

echo "[n8n-reset] remove volume: $volume_name"
docker volume rm "$volume_name" >/dev/null

echo "[n8n-reset] start fresh n8n"
docker compose up -d n8n >/dev/null

echo "[n8n-reset] bootstrap smoke workflow"
for attempt in 1 2 3; do
  if bash scripts/n8n/bootstrap-local-webhook.sh >/tmp/n8n_reset_smoke_bootstrap.log 2>&1; then
    cat /tmp/n8n_reset_smoke_bootstrap.log
    break
  fi
  cat /tmp/n8n_reset_smoke_bootstrap.log
  if [[ "$attempt" == "3" ]]; then
    echo "[n8n-reset] smoke bootstrap failed after retries" >&2
    exit 1
  fi
  echo "[n8n-reset] retry smoke bootstrap ($attempt/3)"
  sleep 3
done

echo "[n8n-reset] bootstrap hermes workflow (force import)"
for attempt in 1 2 3; do
  if N8N_HERMES_FORCE_IMPORT=true bash scripts/n8n/bootstrap-hermes-daily-briefing.sh >/tmp/n8n_reset_hermes_bootstrap.log 2>&1; then
    cat /tmp/n8n_reset_hermes_bootstrap.log
    break
  fi
  cat /tmp/n8n_reset_hermes_bootstrap.log
  if [[ "$attempt" == "3" ]]; then
    echo "[n8n-reset] hermes bootstrap failed after retries" >&2
    exit 1
  fi
  echo "[n8n-reset] retry hermes bootstrap ($attempt/3)"
  sleep 3
done

echo "[n8n-reset] cleanup hermes duplicates (should already be singleton)"
N8N_WORKFLOW_NAME="Hermes Daily Briefing Workflow" bash scripts/n8n/cleanup-duplicate-workflows.sh >/tmp/n8n_reset_cleanup.log
cat /tmp/n8n_reset_cleanup.log

echo "[n8n-reset] done. backup=$BACKUP_DIR"
