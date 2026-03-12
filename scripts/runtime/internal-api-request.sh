#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
source scripts/runtime/load-env.sh

ENV_FILE="${ENV_FILE:-${COMPOSE_ENV_FILE:-${ROOT_DIR}/.env.local}}"
load_runtime_env "$ENV_FILE"

METHOD="${1:-}"
URL="${2:-}"
OUTPUT_FILE="${3:-}"
BODY_FILE="${4:-}"

if [[ -z "$METHOD" || -z "$URL" || -z "$OUTPUT_FILE" ]]; then
  echo "[internal-api] usage: bash scripts/runtime/internal-api-request.sh METHOD URL OUTPUT_FILE [BODY_FILE]" >&2
  exit 1
fi

TOKEN="$(runtime_env_get INTERNAL_API_TOKEN)"
SECRET="$(runtime_env_get INTERNAL_SIGNING_SECRET)"

if [[ -z "$TOKEN" ]] && docker ps --format '{{.Names}}' | grep -qx 'nanoclaw-llm-proxy'; then
  TOKEN="$(docker exec nanoclaw-llm-proxy sh -lc "printenv INTERNAL_API_TOKEN 2>/dev/null || true" | tr -d '\r')"
fi
if [[ -z "$SECRET" ]] && docker ps --format '{{.Names}}' | grep -qx 'nanoclaw-llm-proxy'; then
  SECRET="$(docker exec nanoclaw-llm-proxy sh -lc "printenv INTERNAL_SIGNING_SECRET 2>/dev/null || true" | tr -d '\r')"
fi

if [[ -z "$TOKEN" || -z "$SECRET" ]]; then
  echo "[internal-api] INTERNAL_API_TOKEN / INTERNAL_SIGNING_SECRET must be set" >&2
  exit 1
fi

BODY_PATH="$BODY_FILE"
if [[ -n "$BODY_PATH" && ! -f "$BODY_PATH" ]]; then
  echo "[internal-api] body file not found: $BODY_PATH" >&2
  exit 1
fi

TIMESTAMP="$(date +%s)"
NONCE="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(16))
PY
)"

SIGNATURE="$(
  python3 - <<'PY' "$SECRET" "$TIMESTAMP" "$NONCE" "${BODY_PATH:-}"
import hashlib
import hmac
import sys
from pathlib import Path

secret, timestamp, nonce, body_path = sys.argv[1:5]
body = Path(body_path).read_bytes() if body_path else b""
payload = f"{timestamp}.{nonce}.".encode("utf-8") + body
print(hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest())
PY
)"

headers=(
  -H "x-internal-token: ${TOKEN}"
  -H "x-timestamp: ${TIMESTAMP}"
  -H "x-nonce: ${NONCE}"
  -H "x-signature: ${SIGNATURE}"
)

if [[ -n "$BODY_PATH" ]]; then
  headers+=(-H 'content-type: application/json')
fi

curl_args=(
  -sS
  -o "$OUTPUT_FILE"
  -w '%{http_code}'
  -X "$METHOD"
  "$URL"
  "${headers[@]}"
)

if [[ -n "$BODY_PATH" ]]; then
  curl_args+=(
    --data-binary "@${BODY_PATH}"
  )
fi

curl "${curl_args[@]}"
