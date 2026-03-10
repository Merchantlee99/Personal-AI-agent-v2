#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
source scripts/runtime/compose-env.sh
source scripts/runtime/load-env.sh
load_runtime_env "${COMPOSE_ENV_FILE:-$ROOT_DIR/.env.local}"

API_PORT="${API_PORT:-8001}"
SMOKE_ID="$(date +%Y%m%d-%H%M%S)-$RANDOM"
INBOX_FILE="smoke-${SMOKE_ID}.json"
SIGNED_HELPER="bash scripts/runtime/internal-api-request.sh"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"

wait_for_container_health() {
  local service="$1"
  local retries="${2:-20}"
  local sleep_sec="${3:-1}"
  local container_id=""
  local status=""

  for _ in $(seq 1 "$retries"); do
    container_id="$(docker compose ps -q "$service" 2>/dev/null || true)"
    if [[ -z "$container_id" ]]; then
      sleep "$sleep_sec"
      continue
    fi
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
    if [[ "$status" == "healthy" || "$status" == "running" ]]; then
      return 0
    fi
    sleep "$sleep_sec"
  done

  echo "[smoke] $service is not ready (status=$status container_id=$container_id)" >&2
  compose_cmd logs --tail=40 >/tmp/nanoclaw_smoke_container_logs.txt 2>&1 || true
  cat /tmp/nanoclaw_smoke_container_logs.txt >&2 || true
  return 1
}

inspect_service_security_flags() {
  local service="$1"
  local label="$2"
  local container_id=""
  container_id="$(docker compose ps -q "$service" 2>/dev/null || true)"
  if [[ -z "$container_id" ]]; then
    echo "[smoke] unable to resolve container for service=$service" >&2
    return 1
  fi
  docker inspect "$container_id" --format "${label}: ReadonlyRootfs={{.HostConfig.ReadonlyRootfs}} CapDrop={{json .HostConfig.CapDrop}} SecurityOpt={{json .HostConfig.SecurityOpt}} Networks={{range \$k,\$v := .NetworkSettings.Networks}}{{\$k}} {{end}}"
}

post_chat_expect_200() {
  local body_file="$1"
  local output_file="$2"
  local label="$3"

  for i in 1 2 3 4 5; do
    status="$(
      $SIGNED_HELPER POST "http://127.0.0.1:${API_PORT}/api/chat" "$output_file" "$body_file" || true
    )"
    if [[ "$status" == "200" ]]; then
      cat "$output_file"
      return 0
    fi
    if [[ "$status" == "502" || "$status" == "429" ]]; then
      echo "[smoke] ${label} retry $i -> status=$status"
      sleep 2
      continue
    fi
    echo "[smoke] ${label} unexpected status=$status" >&2
    cat "$output_file" >&2 || true
    return 1
  done

  echo "[smoke] ${label} failed after retries" >&2
  cat "$output_file" >&2 || true
  return 1
}

echo "[smoke] agent config check"
bash scripts/verify/validate-agent-config.sh

echo "[smoke] ensure shared_data writable"
mkdir -p shared_data/{inbox,outbox,archive,logs,verified_inbox,obsidian_vault,shared_memory,queue,workflows}
chmod -R a+rwX shared_data || true

if [[ "${CI:-}" == "true" ]]; then
  echo "[smoke] CI mode: reset compose state for deterministic runtime smoke"
  compose_cmd down -v --remove-orphans >/dev/null 2>&1 || true
fi

echo "[smoke] docker services up (telegram-only runtime, rebuild mutable services)"
services=(llm-proxy nanoclaw-agent n8n)
if [[ -n "$TELEGRAM_BOT_TOKEN" ]]; then
  services+=(telegram-poller)
else
  echo "[smoke] TELEGRAM_BOT_TOKEN not set; skip telegram-poller in CI smoke"
fi
compose_cmd up -d --build "${services[@]}" >/dev/null

echo "[smoke] wait containers ready"
wait_for_container_health llm-proxy 30 1
wait_for_container_health n8n 60 2
wait_for_container_health nanoclaw-agent 20 1
if [[ -n "$TELEGRAM_BOT_TOKEN" ]]; then
  wait_for_container_health telegram-poller 20 1
fi

echo "[smoke] llm-proxy health"
curl -fsS "http://127.0.0.1:${API_PORT}/health" >/tmp/nanoclaw_smoke_health.json
cat /tmp/nanoclaw_smoke_health.json

echo "[smoke] /api/runtime-metrics signed check"
$SIGNED_HELPER GET "http://127.0.0.1:${API_PORT}/api/runtime-metrics" /tmp/nanoclaw_smoke_metrics.json >/tmp/nanoclaw_smoke_metrics.status
if [[ "$(cat /tmp/nanoclaw_smoke_metrics.status)" != "200" ]]; then
  echo "[smoke] expected 200 for signed runtime metrics check" >&2
  cat /tmp/nanoclaw_smoke_metrics.json >&2 || true
  exit 1
fi
cat /tmp/nanoclaw_smoke_metrics.json

echo "[smoke] n8n bootstrap"
bash scripts/n8n/bootstrap-local-webhook.sh >/tmp/nanoclaw_smoke_bootstrap.log
cat /tmp/nanoclaw_smoke_bootstrap.log

echo "[smoke] /api/chat canonical check"
cat >/tmp/nanoclaw_smoke_chat_canonical.body.json <<'JSON'
{"agentId":"minerva","message":"smoke-canonical"}
JSON
post_chat_expect_200 \
  /tmp/nanoclaw_smoke_chat_canonical.body.json \
  /tmp/nanoclaw_smoke_chat_canonical.json \
  "chat canonical"

echo "[smoke] /api/chat legacy alias rejection check"
cat >/tmp/nanoclaw_smoke_chat_alias.body.json <<'JSON'
{"agentId":"ace","message":"smoke-legacy-alias"}
JSON
alias_status="$(
  $SIGNED_HELPER POST "http://127.0.0.1:${API_PORT}/api/chat" /tmp/nanoclaw_smoke_chat_alias_reject.json /tmp/nanoclaw_smoke_chat_alias.body.json || true
)"
if [[ "$alias_status" != "400" ]]; then
  echo "[smoke] expected 400 for legacy alias, got $alias_status" >&2
  cat /tmp/nanoclaw_smoke_chat_alias_reject.json >&2 || true
  exit 1
fi

echo "[smoke] /api/chat legacy history(content) check"
cat >/tmp/nanoclaw_smoke_chat_history.body.json <<'JSON'
{"agentId":"minerva","message":"smoke-history","history":[{"role":"user","content":"legacy-content","at":"2026-03-01T12:00:00Z"}]}
JSON
post_chat_expect_200 \
  /tmp/nanoclaw_smoke_chat_history.body.json \
  /tmp/nanoclaw_smoke_chat_history.json \
  "chat legacy history"

echo "[smoke] n8n webhook check"
curl -fsS -X POST 'http://localhost:5678/webhook/nanoclaw-v2-smoke' \
  -H 'content-type: application/json' \
  -d '{"ping":"pong","source":"smoke-runtime"}' >/tmp/nanoclaw_smoke_webhook.json
cat /tmp/nanoclaw_smoke_webhook.json

echo "[smoke] watchdog check"
cat > "shared_data/inbox/$INBOX_FILE" <<JSON
{"agent_id":"hermes","message":"smoke runtime watchdog","source":"smoke-runtime"}
JSON

processed=0
for _ in 1 2 3 4 5 6; do
  if ls shared_data/outbox | grep -q "$INBOX_FILE"; then
    processed=1
    break
  fi
  sleep 1
done

if [[ "$processed" != "1" ]]; then
  echo "[smoke] watchdog output not found for $INBOX_FILE" >&2
  ls -1 shared_data/outbox >&2 || true
  exit 1
fi

latest_outbox="$(ls -1t shared_data/outbox | grep "$INBOX_FILE" | head -n 1)"
cat "shared_data/outbox/$latest_outbox"

python3 - <<'PY' "shared_data/outbox/$latest_outbox"
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
vault_file = payload.get("vault_file")
if isinstance(vault_file, str) and vault_file.startswith("runtime_agent_notes/"):
    target = pathlib.Path("shared_data") / vault_file
    if target.exists():
        target.unlink()
PY
rm -f "shared_data/outbox/$latest_outbox"
find shared_data/archive -type f -name "*-${INBOX_FILE}" -delete

echo "[smoke] security flags check"
inspect_service_security_flags nanoclaw-agent agent
inspect_service_security_flags llm-proxy proxy
inspect_service_security_flags n8n n8n

echo "[smoke] PASS"
