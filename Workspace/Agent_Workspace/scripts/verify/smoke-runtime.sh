#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

FRONTEND_PORT="${FRONTEND_PORT:-3032}"
NEXT_LOG="/tmp/nanoclaw_next_smoke_${FRONTEND_PORT}.log"
SMOKE_ID="$(date +%Y%m%d-%H%M%S)-$RANDOM"
INBOX_FILE="smoke-${SMOKE_ID}.json"

cleanup() {
  if [[ -n "${NEXT_PID:-}" ]]; then
    kill "$NEXT_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

wait_for_container_health() {
  local container="$1"
  local retries="${2:-20}"
  local sleep_sec="${3:-1}"

  for i in $(seq 1 "$retries"); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)"
    if [[ "$status" == "healthy" || "$status" == "running" ]]; then
      return 0
    fi
    sleep "$sleep_sec"
  done

  echo "[smoke] $container is not ready (status=$status)" >&2
  docker compose logs --tail=40 >/tmp/nanoclaw_smoke_container_logs.txt 2>&1 || true
  cat /tmp/nanoclaw_smoke_container_logs.txt >&2 || true
  return 1
}

echo "[smoke] agent config check"
bash scripts/verify/validate-agent-config.sh

echo "[smoke] ui structure check"
bash scripts/verify/check-ui-structure.sh

echo "[smoke] ui inline style budget check"
bash scripts/verify/check-inline-style-budget.sh

echo "[smoke] docker services up"
docker compose up -d llm-proxy nanoclaw-agent n8n >/dev/null

echo "[smoke] wait containers ready"
wait_for_container_health nanoclaw-llm-proxy 20 1
wait_for_container_health nanoclaw-n8n 30 1
wait_for_container_health nanoclaw-agent 20 1

echo "[smoke] llm-proxy health"
health_ready=0
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:8001/health >/tmp/nanoclaw_smoke_health.json; then
    health_ready=1
    break
  fi
  sleep 1
done

if [[ "$health_ready" != "1" ]]; then
  echo "[smoke] llm-proxy health check failed after retries" >&2
  exit 1
fi
cat /tmp/nanoclaw_smoke_health.json

echo "[smoke] n8n bootstrap"
bash scripts/n8n/bootstrap-local-webhook.sh >/tmp/nanoclaw_smoke_bootstrap.log
cat /tmp/nanoclaw_smoke_bootstrap.log

echo "[smoke] start next dev on :$FRONTEND_PORT"
npm run dev -- --hostname 127.0.0.1 --port "$FRONTEND_PORT" >"$NEXT_LOG" 2>&1 &
NEXT_PID=$!

ready=0
for i in 1 2 3 4 5 6 7 8 9 10; do
  code="$(curl -s -o /tmp/nanoclaw_smoke_home.html -w '%{http_code}' "http://127.0.0.1:${FRONTEND_PORT}/" || true)"
  if [[ "$code" == "200" || "$code" == "404" ]]; then
    ready=1
    break
  fi
  sleep 1
done

if [[ "$ready" != "1" ]]; then
  echo "[smoke] next dev did not become ready" >&2
  tail -n 50 "$NEXT_LOG" >&2 || true
  exit 1
fi

echo "[smoke] /api/chat alias check"
curl -fsS -X POST "http://127.0.0.1:${FRONTEND_PORT}/api/chat" \
  -H 'content-type: application/json' \
  -d '{"agentId":"ace","message":"smoke-alias"}' >/tmp/nanoclaw_smoke_chat_alias.json
cat /tmp/nanoclaw_smoke_chat_alias.json

echo "[smoke] /api/chat legacy history(content) check"
curl -fsS -X POST "http://127.0.0.1:${FRONTEND_PORT}/api/chat" \
  -H 'content-type: application/json' \
  -d '{"agentId":"minerva","message":"smoke-history","history":[{"role":"user","content":"legacy-content","at":"2026-03-01T12:00:00Z"}]}' >/tmp/nanoclaw_smoke_chat_history.json
cat /tmp/nanoclaw_smoke_chat_history.json

echo "[smoke] n8n webhook check"
curl -fsS -X POST 'http://localhost:5678/webhook/nanoclaw-v2-smoke' \
  -H 'content-type: application/json' \
  -d '{"ping":"pong","source":"smoke-runtime"}' >/tmp/nanoclaw_smoke_webhook.json
cat /tmp/nanoclaw_smoke_webhook.json

echo "[smoke] watchdog check"
cat > "shared_data/inbox/$INBOX_FILE" <<JSON
{"agent_id":"owl","message":"smoke watchdog","source":"smoke-runtime"}
JSON

processed=0
for i in 1 2 3 4 5 6; do
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
