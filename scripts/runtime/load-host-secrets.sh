#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "[host-secrets] source this file from another script" >&2
  exit 1
fi

host_secret_command_exists() {
  command -v "$1" >/dev/null 2>&1
}

host_secret_keychain_read() {
  local service="$1"
  local account="$2"
  security find-generic-password -s "$service" -a "$account" -w 2>/dev/null
}

host_secret_op_read() {
  local ref="$1"
  op read "$ref" 2>/dev/null
}

resolve_host_secret() {
  local key="$1"
  local current="${!key:-}"
  local op_ref_var="${key}_OP_REF"
  local service_var="${key}_KEYCHAIN_SERVICE"
  local account_var="${key}_KEYCHAIN_ACCOUNT"
  local op_ref="${!op_ref_var:-}"
  local service="${!service_var:-}"
  local account="${!account_var:-}"
  local value=""

  if [[ -n "$current" ]]; then
    return 0
  fi

  if [[ -n "$op_ref" ]]; then
    if ! host_secret_command_exists op; then
      echo "[host-secrets] WARN op CLI missing for $key" >&2
      return 1
    fi
    value="$(host_secret_op_read "$op_ref" || true)"
  elif [[ -n "$service" && -n "$account" ]]; then
    if ! host_secret_command_exists security; then
      echo "[host-secrets] WARN macOS security CLI missing for $key" >&2
      return 1
    fi
    value="$(host_secret_keychain_read "$service" "$account" || true)"
  else
    return 0
  fi

  if [[ -z "$value" ]]; then
    echo "[host-secrets] WARN unable to resolve $key from host secret store" >&2
    return 1
  fi

  export "$key=$value"
  return 0
}

load_default_host_secrets() {
  local keys=(
    INTERNAL_API_TOKEN
    INTERNAL_SIGNING_SECRET
    GEMINI_API_KEY
    GOOGLE_API_KEY
    ANTHROPIC_API_KEY
    TAVILY_API_KEY
    TELEGRAM_BOT_TOKEN
    TELEGRAM_WEBHOOK_SECRET
    GOOGLE_CALENDAR_OAUTH_CLIENT_ID
    GOOGLE_CALENDAR_OAUTH_CLIENT_SECRET
    DEEPL_API_KEY
    NOTEBOOKLM_API_KEY
    N8N_ENCRYPTION_KEY
    N8N_BASIC_AUTH_USER
    N8N_BASIC_AUTH_PASSWORD
  )
  local key
  for key in "${keys[@]}"; do
    resolve_host_secret "$key" || true
  done
}
