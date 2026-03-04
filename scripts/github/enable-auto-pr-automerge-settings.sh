#!/usr/bin/env bash
set -euo pipefail

REPO="${GITHUB_REPO:-Merchantlee99/Personal-AI-agent-v2}"
TOKEN_RAW="${GITHUB_TOKEN:-}"
TOKEN="$(printf '%s' "$TOKEN_RAW" | tr -d '\r\n')"
API_BASE="https://api.github.com"
TMP_RESPONSE="$(mktemp)"

cleanup() {
  rm -f "$TMP_RESPONSE"
}
trap cleanup EXIT

if [[ -z "$TOKEN" ]]; then
  echo "[github-auto-merge] missing GITHUB_TOKEN" >&2
  echo "[github-auto-merge] usage:" >&2
  echo "  GITHUB_TOKEN=*** GITHUB_REPO=${REPO} $0" >&2
  exit 1
fi

if [[ "$TOKEN" == "..." ]]; then
  echo "[github-auto-merge] invalid GITHUB_TOKEN placeholder ('...'). use a real token." >&2
  exit 1
fi

request() {
  local method="$1"
  local url="$2"
  local data="${3:-}"

  if [[ -n "$data" ]]; then
    curl -sS -o "$TMP_RESPONSE" -w '%{http_code}' -X "$method" \
      "$url" \
      -H "Accept: application/vnd.github+json" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      -d "$data"
  else
    curl -sS -o "$TMP_RESPONSE" -w '%{http_code}' -X "$method" \
      "$url" \
      -H "Accept: application/vnd.github+json" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "X-GitHub-Api-Version: 2022-11-28"
  fi
}

assert_2xx() {
  local status="$1"
  local step="$2"
  if [[ ! "$status" =~ ^2 ]]; then
    echo "[github-auto-merge] failed step=${step} status=${status}" >&2
    cat "$TMP_RESPONSE" >&2 || true
    exit 1
  fi
}

echo "[github-auto-merge] validating token"
status="$(request GET "${API_BASE}/user")"
assert_2xx "$status" "user"

AUTH_LOGIN="$(python3 - <<'PY' "$TMP_RESPONSE"
import json, sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(payload.get("login") or "")
PY
)"
if [[ -z "$AUTH_LOGIN" ]]; then
  echo "[github-auto-merge] failed: empty auth login" >&2
  exit 1
fi
echo "[github-auto-merge] authenticated_as=${AUTH_LOGIN}"

echo "[github-auto-merge] checking repo admin permission"
status="$(request GET "${API_BASE}/repos/${REPO}")"
assert_2xx "$status" "repo"
python3 - <<'PY' "$TMP_RESPONSE"
import json, sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
admin = (payload.get("permissions") or {}).get("admin")
print(f"[github-auto-merge] repo_admin={admin}")
if admin is False:
    print("[github-auto-merge] admin permission required", file=sys.stderr)
    raise SystemExit(3)
PY

if [[ "$?" -ne 0 ]]; then
  exit 1
fi

echo "[github-auto-merge] enabling repository allow_auto_merge"
status="$(request PATCH "${API_BASE}/repos/${REPO}" '{"allow_auto_merge":true}')"
assert_2xx "$status" "repo_patch_allow_auto_merge"

echo "[github-auto-merge] setting actions workflow permissions (write + can_approve_pull_request_reviews)"
status="$(request PUT "${API_BASE}/repos/${REPO}/actions/permissions/workflow" '{"default_workflow_permissions":"write","can_approve_pull_request_reviews":true}')"
assert_2xx "$status" "actions_permissions_workflow"

echo "[github-auto-merge] reading back actions workflow permissions"
status="$(request GET "${API_BASE}/repos/${REPO}/actions/permissions/workflow")"
assert_2xx "$status" "actions_permissions_workflow_get"
python3 - <<'PY' "$TMP_RESPONSE"
import json, sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(f"[github-auto-merge] default_workflow_permissions={payload.get('default_workflow_permissions')}")
print(f"[github-auto-merge] can_approve_pull_request_reviews={payload.get('can_approve_pull_request_reviews')}")
PY

echo "[github-auto-merge] done"
