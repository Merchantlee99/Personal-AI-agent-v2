#!/usr/bin/env bash
set -euo pipefail

REPO="${GITHUB_REPO:-Merchantlee99/Personal-AI-agent-v2}"
BRANCH="${GITHUB_BRANCH:-main}"
TOKEN="${GITHUB_TOKEN:-}"
CHECK_CONTEXT="${GITHUB_REQUIRED_CHECK:-runtime-verification}"

if [[ -z "$TOKEN" ]]; then
  echo "[branch-protection] missing GITHUB_TOKEN" >&2
  echo "[branch-protection] set env then re-run:" >&2
  echo "  GITHUB_TOKEN=*** GITHUB_REPO=${REPO} GITHUB_BRANCH=${BRANCH} $0" >&2
  exit 1
fi

if [[ "$TOKEN" == "..." ]]; then
  echo "[branch-protection] invalid GITHUB_TOKEN placeholder ('...'). use a real token." >&2
  exit 1
fi

echo "[branch-protection] applying to ${REPO}:${BRANCH}"

tmp_response="$(mktemp)"
http_status="$(
  curl -sS -o "$tmp_response" -w '%{http_code}' -X PUT \
    "https://api.github.com/repos/${REPO}/branches/${BRANCH}/protection" \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -d @- <<JSON
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["${CHECK_CONTEXT}"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": true
}
JSON
)"

if [[ "$http_status" != "200" ]]; then
  echo "[branch-protection] failed status=${http_status}" >&2
  cat "$tmp_response" >&2 || true
  rm -f "$tmp_response"
  exit 1
fi

python3 - <<'PY' "$tmp_response"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
checks = payload.get("required_status_checks") or {}
contexts = checks.get("contexts") or []
print(f"[branch-protection] required_status_checks.strict={checks.get('strict')}")
print(f"[branch-protection] required_status_checks.contexts={contexts}")
print(f"[branch-protection] enforce_admins.enabled={(payload.get('enforce_admins') or {}).get('enabled')}")
print(
    "[branch-protection] required_pull_request_reviews.approvals="
    f"{(payload.get('required_pull_request_reviews') or {}).get('required_approving_review_count')}"
)
PY

rm -f "$tmp_response"

echo "[branch-protection] done"
