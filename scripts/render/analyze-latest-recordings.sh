#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

DESKTOP_DIR="${DESKTOP_DIR:-$HOME/Desktop}"

MOV_LIST="$(ls -1t "$DESKTOP_DIR"/*.mov 2>/dev/null | head -n 2 || true)"
CURRENT_VIDEO="$(printf '%s\n' "$MOV_LIST" | sed -n '1p')"
REFERENCE_VIDEO="$(printf '%s\n' "$MOV_LIST" | sed -n '2p')"

if [[ -z "$CURRENT_VIDEO" || -z "$REFERENCE_VIDEO" ]]; then
  echo "[render-analyze-latest] need at least two .mov files in $DESKTOP_DIR" >&2
  exit 1
fi

echo "[render-analyze-latest] reference=$REFERENCE_VIDEO"
echo "[render-analyze-latest] current=$CURRENT_VIDEO"

REFERENCE_VIDEO="$REFERENCE_VIDEO" CURRENT_VIDEO="$CURRENT_VIDEO" bash scripts/render/analyze-orb-match.sh
