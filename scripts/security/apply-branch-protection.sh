#!/usr/bin/env bash
set -euo pipefail

REPO="${GITHUB_REPO:-Merchantlee99/Personal-AI-agent-v2}"
BRANCH="${GITHUB_BRANCH:-main}"
TOKEN_RAW="${GITHUB_TOKEN:-}"
TOKEN="$(printf '%s' "$TOKEN_RAW" | tr -d '\r\n')"
CHECK_CONTEXT="${GITHUB_REQUIRED_CHECK:-runtime-verification}"
PROFILE_RAW="${PROTECTION_PROFILE:-strict}"
PROFILE="$(printf '%s' "$PROFILE_RAW" | tr '[:upper:]' '[:lower:]')"
APPROVALS_OVERRIDE="${GITHUB_REQUIRED_APPROVALS:-}"
API_BASE="https://api.github.com"
REPO_OWNER="${REPO%%/*}"
AUTH_LOGIN=""
REQUIRED_APPROVALS=""
tmp_response="$(mktemp)"

cleanup() {
  rm -f "$tmp_response"
}
trap cleanup EXIT

print_error_body() {
  cat "$tmp_response" >&2 || true
}

print_status_hint() {
  local status="$1"
  case "$status" in
    401)
      echo "[branch-protection] hint: 401은 기존 보호 규칙 충돌이 아니라 토큰 인증 실패입니다." >&2
      echo "[branch-protection] hint: 토큰 만료/오타, 앞뒤 공백·개행 포함 여부를 확인하세요." >&2
      echo "[branch-protection] hint: fine-grained PAT은 대상 저장소 Administration(read/write) 권한이 필요합니다." >&2
      ;;
    403)
      echo "[branch-protection] hint: 토큰은 유효하지만 저장소 admin 권한이 없거나 SSO 승인이 필요할 수 있습니다." >&2
      ;;
    404)
      echo "[branch-protection] hint: 저장소/브랜치 값이 잘못되었거나 토큰 접근 권한이 부족할 수 있습니다." >&2
      ;;
  esac
}

resolve_required_approvals() {
  if [[ -n "$APPROVALS_OVERRIDE" ]]; then
    if [[ ! "$APPROVALS_OVERRIDE" =~ ^[0-9]+$ ]]; then
      echo "[branch-protection] invalid GITHUB_REQUIRED_APPROVALS=${APPROVALS_OVERRIDE} (must be integer)" >&2
      exit 1
    fi
    REQUIRED_APPROVALS="$APPROVALS_OVERRIDE"
    return
  fi

  case "$PROFILE" in
    strict | team | default)
      REQUIRED_APPROVALS=1
      ;;
    solo | single | single-operator | one-person | oneperson | 1p)
      REQUIRED_APPROVALS=0
      ;;
    auto)
      if [[ "${AUTH_LOGIN,,}" == "${REPO_OWNER,,}" ]]; then
        REQUIRED_APPROVALS=0
      else
        REQUIRED_APPROVALS=1
      fi
      ;;
    *)
      echo "[branch-protection] invalid PROTECTION_PROFILE=${PROFILE_RAW}" >&2
      echo "[branch-protection] allowed profiles: strict, solo, auto" >&2
      exit 1
      ;;
  esac
}

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

echo "[branch-protection] preflight: validating token"
http_status="$(
  curl -sS -o "$tmp_response" -w '%{http_code}' \
    "${API_BASE}/user" \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28"
)"
if [[ "$http_status" != "200" ]]; then
  echo "[branch-protection] failed preflight(user) status=${http_status}" >&2
  print_error_body
  print_status_hint "$http_status"
  exit 1
fi
AUTH_LOGIN="$(python3 - <<'PY' "$tmp_response"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(payload.get("login") or "")
PY
)"
if [[ -z "$AUTH_LOGIN" ]]; then
  echo "[branch-protection] failed preflight(user): empty login" >&2
  exit 1
fi
echo "[branch-protection] authenticated_as=${AUTH_LOGIN}"

echo "[branch-protection] preflight: checking repository access"
http_status="$(
  curl -sS -o "$tmp_response" -w '%{http_code}' \
    "${API_BASE}/repos/${REPO}" \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28"
)"
if [[ "$http_status" != "200" ]]; then
  echo "[branch-protection] failed preflight(repo) status=${http_status}" >&2
  print_error_body
  print_status_hint "$http_status"
  exit 1
fi

repo_admin_check_rc=0
python3 - <<'PY' "$tmp_response" || repo_admin_check_rc=$?
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
permissions = payload.get("permissions") or {}
admin = permissions.get("admin")
print(f"[branch-protection] repo_admin={admin}")
if admin is False:
    print("[branch-protection] insufficient permission: repository admin is required.", file=sys.stderr)
    raise SystemExit(3)
PY
if [[ "$repo_admin_check_rc" == "3" ]]; then
  exit 1
fi
if [[ "$repo_admin_check_rc" != "0" ]]; then
  echo "[branch-protection] failed parsing repository permission payload" >&2
  exit 1
fi

resolve_required_approvals
echo "[branch-protection] profile=${PROFILE} required_approvals=${REQUIRED_APPROVALS}"

echo "[branch-protection] preflight: checking branch existence"
http_status="$(
  curl -sS -o "$tmp_response" -w '%{http_code}' \
    "${API_BASE}/repos/${REPO}/branches/${BRANCH}" \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28"
)"
if [[ "$http_status" != "200" ]]; then
  echo "[branch-protection] failed preflight(branch) status=${http_status}" >&2
  print_error_body
  print_status_hint "$http_status"
  exit 1
fi

echo "[branch-protection] preflight: checking existing protection"
http_status="$(
  curl -sS -o "$tmp_response" -w '%{http_code}' \
    "${API_BASE}/repos/${REPO}/branches/${BRANCH}/protection" \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28"
)"
if [[ "$http_status" == "200" ]]; then
  python3 - <<'PY' "$tmp_response"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
checks = payload.get("required_status_checks") or {}
contexts = checks.get("contexts") or []
print(f"[branch-protection] existing_protection=true contexts={contexts}")
PY
elif [[ "$http_status" == "404" ]]; then
  echo "[branch-protection] existing_protection=false (will create)"
else
  echo "[branch-protection] failed preflight(protection) status=${http_status}" >&2
  print_error_body
  print_status_hint "$http_status"
  exit 1
fi

http_status="$(
  curl -sS -o "$tmp_response" -w '%{http_code}' -X PUT \
    "${API_BASE}/repos/${REPO}/branches/${BRANCH}/protection" \
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
    "required_approving_review_count": ${REQUIRED_APPROVALS}
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
  print_error_body
  print_status_hint "$http_status"
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

echo "[branch-protection] done"
