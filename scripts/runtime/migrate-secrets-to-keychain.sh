#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${1:-${ROOT_DIR}/.env.local}"
KEYCHAIN_SERVICE="${KEYCHAIN_SERVICE:-nanoclaw}"
BACKUP_SUFFIX="${BACKUP_SUFFIX:-.bak-keychain}"
KEEP_BACKUP="${KEEP_BACKUP:-false}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[migrate-keychain] env file missing: $ENV_FILE" >&2
  exit 1
fi

if ! command -v security >/dev/null 2>&1; then
  echo "[migrate-keychain] macOS security CLI missing" >&2
  exit 1
fi

keys=(
  INTERNAL_API_TOKEN
  INTERNAL_SIGNING_SECRET
  GOOGLE_API_KEY
  ANTHROPIC_API_KEY
  TAVILY_API_KEY
  TELEGRAM_BOT_TOKEN
  TELEGRAM_WEBHOOK_SECRET
  GOOGLE_CALENDAR_OAUTH_CLIENT_SECRET
  DEEPL_API_KEY
  NOTEBOOKLM_API_KEY
  N8N_ENCRYPTION_KEY
  N8N_BASIC_AUTH_USER
  N8N_BASIC_AUTH_PASSWORD
)

backup_path="${ENV_FILE}${BACKUP_SUFFIX}"
if [[ "$KEEP_BACKUP" == "true" ]]; then
  cp "$ENV_FILE" "$backup_path"
  chmod 600 "$backup_path"
fi

tmp_file="$(mktemp)"
cp "$ENV_FILE" "$tmp_file"

read_env_value() {
  local key="$1"
  awk -F= -v key="$key" '$1==key {sub(/^[^=]*=/, ""); print; exit}' "$tmp_file"
}

set_env_value() {
  local key="$1"
  local value="$2"
  python3 - "$tmp_file" "$key" "$value" <<'PY'
import sys
from pathlib import Path
path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text(encoding='utf-8').splitlines()
out = []
found = False
for line in lines:
    if line.startswith(f"{key}="):
        out.append(f"{key}={value}")
        found = True
    else:
        out.append(line)
if not found:
    out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding='utf-8')
PY
}

ensure_keychain_ref() {
  local key="$1"
  local service_var="${key}_KEYCHAIN_SERVICE"
  local account_var="${key}_KEYCHAIN_ACCOUNT"
  set_env_value "$service_var" "$KEYCHAIN_SERVICE"
  set_env_value "$account_var" "$key"
}

for key in "${keys[@]}"; do
  value="$(read_env_value "$key" || true)"
  if [[ -z "$value" ]]; then
    continue
  fi
  if [[ "$value" == op://* ]]; then
    continue
  fi
  security add-generic-password -U -s "$KEYCHAIN_SERVICE" -a "$key" -w "$value" >/dev/null
  set_env_value "$key" ""
  ensure_keychain_ref "$key"
  echo "[migrate-keychain] stored $key in Keychain service=$KEYCHAIN_SERVICE account=$key"
done

mv "$tmp_file" "$ENV_FILE"
echo "[migrate-keychain] updated $ENV_FILE"
if [[ "$KEEP_BACKUP" == "true" ]]; then
  echo "[migrate-keychain] backup saved to $backup_path"
else
  echo "[migrate-keychain] plaintext backup disabled (KEEP_BACKUP=false)"
fi
