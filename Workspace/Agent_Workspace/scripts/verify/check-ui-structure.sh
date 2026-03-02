#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

MAIN_FILE="src/components/chat-dashboard.tsx"
MAX_MAIN_LINES=250

required_files=(
  "src/components/chat-dashboard/composer.tsx"
  "src/components/chat-dashboard/empty-state.tsx"
  "src/components/chat-dashboard/history-panel.tsx"
  "src/components/chat-dashboard/mini-shape.tsx"
  "src/components/chat-dashboard/sidebar.tsx"
  "src/components/chat-dashboard/theme.ts"
  "src/components/chat-dashboard/types.ts"
  "src/components/chat-dashboard/quick-commands.ts"
)

if [[ ! -f "$MAIN_FILE" ]]; then
  echo "[ui-structure] missing: $MAIN_FILE" >&2
  exit 1
fi

main_lines="$(wc -l < "$MAIN_FILE" | tr -d ' ')"
if [[ "$main_lines" -gt "$MAX_MAIN_LINES" ]]; then
  echo "[ui-structure] $MAIN_FILE is too large ($main_lines > $MAX_MAIN_LINES)" >&2
  exit 1
fi

for file in "${required_files[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "[ui-structure] missing split component: $file" >&2
    exit 1
  fi
done

echo "[ui-structure] OK: $MAIN_FILE=$main_lines lines"
