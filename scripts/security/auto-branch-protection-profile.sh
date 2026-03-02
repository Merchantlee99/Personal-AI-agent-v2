#!/usr/bin/env bash
set -euo pipefail

REPO="${GITHUB_REPO:-Merchantlee99/Personal-AI-agent-v2}"
BRANCH="${GITHUB_BRANCH:-main}"
TOKEN_RAW="${GITHUB_TOKEN:-}"
TOKEN="$(printf '%s' "$TOKEN_RAW" | tr -d '\r\n')"
STRICT_THRESHOLD="${OPERATOR_STRICT_THRESHOLD:-2}"
API_BASE="https://api.github.com"
tmp_response="$(mktemp)"

cleanup() {
  rm -f "$tmp_response"
}
trap cleanup EXIT

if [[ -z "$TOKEN" ]]; then
  echo "[auto-branch-protection] missing GITHUB_TOKEN" >&2
  exit 1
fi

if [[ "$TOKEN" == "..." ]]; then
  echo "[auto-branch-protection] invalid GITHUB_TOKEN placeholder ('...'). use a real token." >&2
  exit 1
fi

if [[ ! "$STRICT_THRESHOLD" =~ ^[0-9]+$ ]]; then
  echo "[auto-branch-protection] invalid OPERATOR_STRICT_THRESHOLD=${STRICT_THRESHOLD}" >&2
  exit 1
fi

echo "[auto-branch-protection] repo=${REPO} branch=${BRANCH} strict_threshold=${STRICT_THRESHOLD}"

http_status="$(
  curl -sS -o "$tmp_response" -w '%{http_code}' \
    "${API_BASE}/repos/${REPO}/collaborators?per_page=100&affiliation=all" \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28"
)"

if [[ "$http_status" != "200" ]]; then
  echo "[auto-branch-protection] failed to list collaborators status=${http_status}" >&2
  cat "$tmp_response" >&2 || true
  exit 1
fi

operator_count="$(python3 - <<'PY' "$tmp_response"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
count = 0
for row in payload:
    perms = row.get("permissions") or {}
    if perms.get("push"):
        count += 1
print(count)
PY
)"

if [[ ! "$operator_count" =~ ^[0-9]+$ ]]; then
  echo "[auto-branch-protection] failed to parse operator count" >&2
  exit 1
fi

profile="solo"
if (( operator_count >= STRICT_THRESHOLD )); then
  profile="strict"
fi

echo "[auto-branch-protection] operator_count=${operator_count} -> profile=${profile}"

PROTECTION_PROFILE="${profile}" \
GITHUB_TOKEN="${TOKEN}" \
GITHUB_REPO="${REPO}" \
GITHUB_BRANCH="${BRANCH}" \
bash scripts/security/apply-branch-protection.sh

