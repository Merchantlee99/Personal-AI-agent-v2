#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

HERMES_FORCE_IMPORT="${N8N_HERMES_FORCE_IMPORT:-false}"

N8N_BOOTSTRAP_WORKFLOW_NAME="Hermes Daily Briefing Workflow" \
N8N_BOOTSTRAP_WEBHOOK_PATH="hermes-daily-briefing" \
N8N_BOOTSTRAP_FORCE_IMPORT="$HERMES_FORCE_IMPORT" \
bash scripts/n8n/bootstrap-local-webhook.sh n8n/workflows/hermes-daily-briefing.json
