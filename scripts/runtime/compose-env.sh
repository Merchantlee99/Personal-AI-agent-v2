#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "[compose-env] source this file from another script" >&2
  exit 1
fi

REPO_ROOT="${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-${REPO_ROOT}/.env.local}"
source "${REPO_ROOT}/scripts/runtime/load-host-secrets.sh"

compose_cmd() {
  if [[ ! -f "$COMPOSE_ENV_FILE" ]]; then
    echo "[compose-env] missing compose env file: $COMPOSE_ENV_FILE" >&2
    return 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$COMPOSE_ENV_FILE"
  set +a
  load_default_host_secrets
  docker compose --env-file "$COMPOSE_ENV_FILE" "$@"
}
