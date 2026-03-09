#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
source scripts/runtime/compose-env.sh

if [[ ! -f .env.local ]]; then
  echo "[llm-runtime] .env.local not found" >&2
  exit 1
fi

get_env_value() {
  local key="$1"
  awk -F= -v key="$key" '
    $0 ~ /^[[:space:]]*#/ {next}
    $1 ~ "^[[:space:]]*"key"[[:space:]]*$" {
      v=$2
      sub(/^[[:space:]]+/, "", v)
      sub(/[[:space:]]+$/, "", v)
      gsub(/^["'\'']|["'\'']$/, "", v)
      print v
      exit
    }
  ' .env.local
}

LLM_PROVIDER_RAW="$(get_env_value LLM_PROVIDER || true)"
LLM_PROVIDER="${LLM_PROVIDER_RAW:-auto}"
GEMINI_KEY="$(get_env_value GEMINI_API_KEY || true)"
GOOGLE_KEY="$(get_env_value GOOGLE_API_KEY || true)"
ANTHROPIC_KEY="$(get_env_value ANTHROPIC_API_KEY || true)"

HAS_KEY=0
if [[ -n "${GEMINI_KEY}" || -n "${GOOGLE_KEY}" || -n "${ANTHROPIC_KEY}" ]]; then
  HAS_KEY=1
fi
HAS_GEMINI_KEY=0
if [[ -n "${GEMINI_KEY}" || -n "${GOOGLE_KEY}" ]]; then
  HAS_GEMINI_KEY=1
fi

echo "[llm-runtime] provider=$LLM_PROVIDER has_key=$HAS_KEY"

if [[ "$LLM_PROVIDER" == "gemini" && "$HAS_GEMINI_KEY" != "1" ]]; then
  echo "[llm-runtime] FAIL: LLM_PROVIDER=gemini but GEMINI_API_KEY/GOOGLE_API_KEY is missing" >&2
  exit 1
fi

if [[ "$LLM_PROVIDER" == "anthropic" && -z "${ANTHROPIC_KEY}" ]]; then
  echo "[llm-runtime] FAIL: LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is missing" >&2
  exit 1
fi

if [[ "$LLM_PROVIDER" == "auto" && "$HAS_KEY" != "1" ]]; then
  echo "[llm-runtime] WARN: auto mode without key -> mock fallback path is expected"
fi

echo "[llm-runtime] ensure llm-proxy up"
compose_cmd up -d llm-proxy >/dev/null

echo "[llm-runtime] wait /health"
ok=0
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:8001/health >/tmp/llm_runtime_health.json 2>/dev/null; then
    ok=1
    break
  fi
  sleep 1
done
if [[ "$ok" != "1" ]]; then
  echo "[llm-runtime] FAIL: llm-proxy health check failed" >&2
  exit 1
fi
cat /tmp/llm_runtime_health.json

echo "[llm-runtime] signed /api/agent probe"
python3 - <<'PY'
import hashlib
import hmac
import json
import subprocess
import time
from pathlib import Path


def load_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


env = load_env(Path(".env.local"))
token = env.get("INTERNAL_API_TOKEN", "change-me-in-env")
secret = env.get("INTERNAL_SIGNING_SECRET", "change-signing-secret")
provider = (env.get("LLM_PROVIDER", "auto") or "auto").strip().lower()
has_gemini_key = bool((env.get("GEMINI_API_KEY", "") or env.get("GOOGLE_API_KEY", "")).strip())
has_anthropic_key = bool((env.get("ANTHROPIC_API_KEY", "")).strip())

body_obj = {"agent_id": "minerva", "message": "llm runtime check"}
body_json = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":"))
body_bytes = body_json.encode("utf-8")

timestamp = str(int(time.time()))
nonce = f"llm-runtime-{timestamp}"
payload = f"{timestamp}.{nonce}.".encode("utf-8") + body_bytes
signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

cmd = [
    "curl",
    "-sS",
    "-X",
    "POST",
    "http://127.0.0.1:8001/api/agent",
    "-H",
    "content-type: application/json",
    "-H",
    f"x-internal-token: {token}",
    "-H",
    f"x-timestamp: {timestamp}",
    "-H",
    f"x-nonce: {nonce}",
    "-H",
    f"x-signature: {signature}",
    "-d",
    body_json,
]

raw = subprocess.check_output(cmd).decode("utf-8")
parsed = json.loads(raw)

required = ("agent_id", "model", "reply")
missing = [key for key in required if key not in parsed]
if missing:
    raise SystemExit(f"[llm-runtime] FAIL: missing fields in response: {missing}")

if provider == "gemini" and has_gemini_key:
    mode = "gemini-real-candidate"
elif provider == "anthropic" and has_anthropic_key:
    mode = "anthropic-real-candidate"
elif provider == "auto" and (has_gemini_key or has_anthropic_key):
    mode = "auto-real-candidate"
else:
    mode = "mock-or-auto-fallback"
print(json.dumps({"mode": mode, "agent_id": parsed["agent_id"], "model": parsed["model"]}, ensure_ascii=False))
PY

echo "[llm-runtime] PASS"
