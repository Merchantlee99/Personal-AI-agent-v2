#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

HERMES_SEARCH_FORCE_IMPORT="${N8N_HERMES_SEARCH_FORCE_IMPORT:-false}"

N8N_BOOTSTRAP_WORKFLOW_NAME="Hermes Web Search Workflow" \
N8N_BOOTSTRAP_WEBHOOK_PATH="hermes-web-search" \
N8N_BOOTSTRAP_FORCE_IMPORT="$HERMES_SEARCH_FORCE_IMPORT" \
bash scripts/n8n/bootstrap-local-webhook.sh n8n/workflows/hermes-web-search-tavily.json
