#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SHARED_DATA_DIR="$ROOT/shared_data"

report_dir() {
  local path="$1"
  if [[ -e "$path" ]]; then
    du -sh "$path"
  else
    printf '0B\t%s\n' "$path"
  fi
}

count_dirs() {
  local path="$1"
  if [[ -d "$path" ]]; then
    find "$path" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' '
  else
    printf '0'
  fi
}

count_files() {
  local path="$1"
  if [[ -d "$path" ]]; then
    find "$path" -type f | wc -l | tr -d ' '
  else
    printf '0'
  fi
}

printf '[shared-data] usage snapshot\n'
report_dir "$SHARED_DATA_DIR/workflows"
report_dir "$SHARED_DATA_DIR/workflows/backups"
report_dir "$SHARED_DATA_DIR/render_review"
report_dir "$SHARED_DATA_DIR/logs"
report_dir "$SHARED_DATA_DIR/archive"
report_dir "$SHARED_DATA_DIR/outbox"
report_dir "$SHARED_DATA_DIR/verified_inbox"
report_dir "$SHARED_DATA_DIR/shared_memory"

printf '\n[shared-data] counts\n'
printf 'workflow_backups=%s\n' "$(count_dirs "$SHARED_DATA_DIR/workflows/backups")"
printf 'render_review_runs=%s\n' "$(count_dirs "$SHARED_DATA_DIR/render_review")"
printf 'log_entries=%s\n' "$(count_files "$SHARED_DATA_DIR/logs")"
printf 'archive_entries=%s\n' "$(count_files "$SHARED_DATA_DIR/archive")"
