#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

INPUT_VIDEO="${INPUT_VIDEO:-}"
OUT_DIR="${OUTPUT_DIR:-shared_data/render_review/frames}"
FPS="${EXTRACT_FPS:-12}"
SIZE="${EXTRACT_SIZE:-1024}"

if [[ -z "$INPUT_VIDEO" ]]; then
  echo "[render-extract] usage:" >&2
  echo "  INPUT_VIDEO=/abs/path/video.mov npm run render:extract" >&2
  exit 1
fi

if [[ ! -f "$INPUT_VIDEO" ]]; then
  echo "[render-extract] missing video: $INPUT_VIDEO" >&2
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "[render-extract] ffmpeg is required" >&2
  exit 1
fi

RUN_ID="$(date +%Y%m%d-%H%M%S)"
FRAME_DIR="$OUT_DIR/$RUN_ID"
mkdir -p "$FRAME_DIR"

ffmpeg -y -i "$INPUT_VIDEO" \
  -vf "fps=$FPS,scale=${SIZE}:${SIZE}:force_original_aspect_ratio=increase,crop=${SIZE}:${SIZE}" \
  "$FRAME_DIR/frame-%05d.png" >/dev/null 2>&1

echo "[render-extract] done"
echo "[render-extract] frame_dir=$FRAME_DIR"
