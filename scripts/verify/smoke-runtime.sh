#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
source scripts/runtime/compose-env.sh

API_PORT="${API_PORT:-8001}"
SMOKE_ID="$(date +%Y%m%d-%H%M%S)-$RANDOM"
INBOX_FILE="smoke-${SMOKE_ID}.json"
SIGNED_HELPER="bash scripts/runtime/internal-api-request.sh"

wait_for_container_health() {
  local container="$1"
  local retries="${2:-20}"
  local sleep_sec="${3:-1}"

  for _ in $(seq 1 "$retries"); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)"
    if [[ "$status" == "healthy" || "$status" == "running" ]]; then
      return 0
    fi
    sleep "$sleep_sec"
  done

  echo "[smoke] $container is not ready (status=$status)" >&2
  compose_cmd logs --tail=40 >/tmp/nanoclaw_smoke_container_logs.txt 2>&1 || true
  cat /tmp/nanoclaw_smoke_container_logs.txt >&2 || true
  return 1
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

echo "[smoke] docker services up (telegram-only runtime, rebuild mutable services)"
compose_cmd up -d --build llm-proxy telegram-poller nanoclaw-agent n8n >/dev/null

echo "[smoke] wait containers ready"
wait_for_container_health nanoclaw-llm-proxy 30 1
wait_for_container_health nanoclaw-n8n 30 1
wait_for_container_health nanoclaw-agent 20 1
wait_for_container_health nanoclaw-telegram-poller 20 1

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
{"agent_id":"clio","message":"smoke watchdog","source":"smoke-runtime"}
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

echo "[smoke] security flags check"
docker inspect nanoclaw-agent --format 'agent: ReadonlyRootfs={{.HostConfig.ReadonlyRootfs}} CapDrop={{json .HostConfig.CapDrop}} SecurityOpt={{json .HostConfig.SecurityOpt}} Networks={{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
docker inspect nanoclaw-llm-proxy --format 'proxy: ReadonlyRootfs={{.HostConfig.ReadonlyRootfs}} CapDrop={{json .HostConfig.CapDrop}} SecurityOpt={{json .HostConfig.SecurityOpt}} Networks={{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
docker inspect nanoclaw-n8n --format 'n8n: ReadonlyRootfs={{.HostConfig.ReadonlyRootfs}} CapDrop={{json .HostConfig.CapDrop}} SecurityOpt={{json .HostConfig.SecurityOpt}} Networks={{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'

echo "[smoke] PASS"
