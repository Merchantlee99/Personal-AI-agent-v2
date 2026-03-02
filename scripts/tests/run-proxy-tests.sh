#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "[tests] building llm-proxy image"
docker compose build llm-proxy >/dev/null

echo "[tests] running proxy unit tests"
docker compose run --rm --no-deps -T \
  -v "$ROOT_DIR/proxy:/workspace" \
  -v "$ROOT_DIR/config:/workspace/config:ro" \
  -w /workspace \
  --entrypoint "" \
  llm-proxy \
  env AGENT_CONFIG_PATH=/workspace/config/agents.json PYTHONPATH=/workspace python -m unittest discover -s tests -p 'test_*.py' -v
