#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
source "${REPO_ROOT}/scripts/runtime/load-env.sh"
load_runtime_env "${REPO_ROOT}/.env.local"

TOKEN="${TELEGRAM_BOT_TOKEN:-}"
if [[ -z "$TOKEN" ]]; then
  echo "[telegram-webhook] TELEGRAM_BOT_TOKEN is required" >&2
  exit 1
fi

echo "[telegram-webhook] getWebhookInfo"
curl -sS "https://api.telegram.org/bot${TOKEN}/getWebhookInfo"
echo
