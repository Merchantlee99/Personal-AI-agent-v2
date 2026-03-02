#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

TARGET_MAIN="src/components/chat-dashboard.tsx"
TARGET_DIR="src/components/chat-dashboard"
MAX_INLINE_STYLES="${MAX_INLINE_STYLES:-35}"

count="$(
  rg -n 'style=\{\{' "$TARGET_MAIN" "$TARGET_DIR" --glob '*.tsx' | wc -l | tr -d ' '
)"

if [[ "$count" -gt "$MAX_INLINE_STYLES" ]]; then
  echo "[ui-inline-style] budget exceeded: $count > $MAX_INLINE_STYLES in $TARGET_MAIN and $TARGET_DIR" >&2
  exit 1
fi

echo "[ui-inline-style] OK: $count <= $MAX_INLINE_STYLES"
