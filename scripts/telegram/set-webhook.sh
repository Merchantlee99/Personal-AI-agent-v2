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

TOKEN="${TELEGRAM_BOT_TOKEN:-}"
SECRET="${TELEGRAM_WEBHOOK_SECRET:-}"
PUBLIC_BASE="${TELEGRAM_WEBHOOK_PUBLIC_BASE:-}"

if [[ -z "$TOKEN" ]]; then
  echo "[telegram-webhook] TELEGRAM_BOT_TOKEN is required" >&2
  exit 1
fi
if [[ -z "$SECRET" ]]; then
  echo "[telegram-webhook] TELEGRAM_WEBHOOK_SECRET is required" >&2
  exit 1
fi
if [[ -z "$PUBLIC_BASE" ]]; then
  echo "[telegram-webhook] TELEGRAM_WEBHOOK_PUBLIC_BASE is required (ex: https://<domain>)" >&2
  exit 1
fi

if [[ "$PUBLIC_BASE" == */api/telegram/webhook ]]; then
  WEBHOOK_URL="$PUBLIC_BASE"
else
  WEBHOOK_URL="${PUBLIC_BASE%/}/api/telegram/webhook"
fi

echo "[telegram-webhook] setWebhook url=${WEBHOOK_URL}"
set_response="$(
  curl -sS -X POST "https://api.telegram.org/bot${TOKEN}/setWebhook" \
    --data-urlencode "url=${WEBHOOK_URL}" \
    --data-urlencode "secret_token=${SECRET}" \
    --data-urlencode "drop_pending_updates=true"
)"
echo "$set_response"

SET_RESPONSE="$set_response" python3 - <<'PY'
import json,sys,os
raw=os.environ.get("SET_RESPONSE","")
try:
    data=json.loads(raw)
except json.JSONDecodeError:
    print("[telegram-webhook] invalid setWebhook response", file=sys.stderr)
    sys.exit(1)
if not data.get("ok"):
    print("[telegram-webhook] setWebhook failed", file=sys.stderr)
    print(raw, file=sys.stderr)
    sys.exit(1)
print("[telegram-webhook] setWebhook success")
PY

echo "[telegram-webhook] getWebhookInfo"
curl -sS "https://api.telegram.org/bot${TOKEN}/getWebhookInfo"
echo
