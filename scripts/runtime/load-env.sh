#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "[runtime-env] source this file from another script" >&2
  exit 1
fi

RUNTIME_ENV_ROOT="${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
source "${RUNTIME_ENV_ROOT}/scripts/runtime/load-host-secrets.sh"

load_runtime_env() {
  local env_file="${1:-${RUNTIME_ENV_ROOT}/.env.local}"
  if [[ ! -f "$env_file" ]]; then
    echo "[runtime-env] env file missing: $env_file" >&2
    return 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
  load_default_host_secrets
}

runtime_env_get() {
  local key="$1"
  printenv "$key" 2>/dev/null || true
}

runtime_env_has_value() {
  local key="$1"
  [[ -n "$(runtime_env_get "$key")" ]]
}
