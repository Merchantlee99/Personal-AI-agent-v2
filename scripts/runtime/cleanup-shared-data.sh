#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SHARED_DATA_DIR="$ROOT/shared_data"

RENDER_REVIEW_RETENTION_DAYS="${SHARED_DATA_RENDER_REVIEW_RETENTION_DAYS:-3}"
WORKFLOW_BACKUP_KEEP_LATEST="${SHARED_DATA_WORKFLOW_BACKUP_KEEP_LATEST:-4}"
LOG_RETENTION_DAYS="${SHARED_DATA_LOG_RETENTION_DAYS:-14}"
DRY_RUN="${DRY_RUN:-0}"

render_review_dir="$SHARED_DATA_DIR/render_review"
workflow_backup_dir="$SHARED_DATA_DIR/workflows/backups"
logs_dir="$SHARED_DATA_DIR/logs"

run_rm() {
  local target="$1"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[shared-data-cleanup] dry-run remove %s\n' "$target"
    return 0
  fi
  rm -rf "$target"
  printf '[shared-data-cleanup] removed %s\n' "$target"
}

cleanup_render_review() {
  [[ -d "$render_review_dir" ]] || return 0
  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    run_rm "$dir"
  done < <(find "$render_review_dir" -mindepth 1 -maxdepth 1 -type d -mtime +"$RENDER_REVIEW_RETENTION_DAYS" | sort)
}

cleanup_workflow_backups() {
  [[ -d "$workflow_backup_dir" ]] || return 0
  local dirs=()
  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    dirs+=("$dir")
  done < <(find "$workflow_backup_dir" -mindepth 1 -maxdepth 1 -type d | sort)
  local total="${#dirs[@]}"
  if (( total <= WORKFLOW_BACKUP_KEEP_LATEST )); then
    return 0
  fi
  local remove_count=$(( total - WORKFLOW_BACKUP_KEEP_LATEST ))
  local i
  for (( i=0; i<remove_count; i++ )); do
    run_rm "${dirs[$i]}"
  done
}

cleanup_logs() {
  [[ -d "$logs_dir" ]] || return 0
  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    run_rm "$path"
  done < <(
    find "$logs_dir" -mindepth 1 -maxdepth 1 \
      \( -name 'daily-verify-*.log' -o -name 'daily-runtime-check-*.log' -o -name 'morning-briefing-report-*.json' -o -name 'morning-briefing-failure-*' \) \
      -mtime +"$LOG_RETENTION_DAYS" | sort
  )
}

printf '[shared-data-cleanup] before\n'
bash "$ROOT/scripts/runtime/report-shared-data-usage.sh"
printf '\n'

cleanup_render_review
cleanup_workflow_backups
cleanup_logs

printf '\n[shared-data-cleanup] after\n'
bash "$ROOT/scripts/runtime/report-shared-data-usage.sh"
