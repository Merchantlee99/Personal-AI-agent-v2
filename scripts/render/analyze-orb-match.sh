#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

REF_VIDEO="${REFERENCE_VIDEO:-}"
CUR_VIDEO="${CURRENT_VIDEO:-}"
OUT_ROOT="${OUTPUT_DIR:-shared_data/render_review}"
FPS="${ANALYZE_FPS:-15}"
SIZE="${ANALYZE_SIZE:-1024}"
FRAME_COUNT="${ANALYZE_FRAME_COUNT:-8}"
WORST_FRAMES="${ANALYZE_WORST_FRAMES:-6}"

if [[ -z "$REF_VIDEO" || -z "$CUR_VIDEO" ]]; then
  echo "[render-analyze] usage:" >&2
  echo "  REFERENCE_VIDEO=/abs/path/ref.mov CURRENT_VIDEO=/abs/path/current.mov npm run render:analyze" >&2
  exit 1
fi

if [[ ! -f "$REF_VIDEO" ]]; then
  echo "[render-analyze] missing reference video: $REF_VIDEO" >&2
  exit 1
fi

if [[ ! -f "$CUR_VIDEO" ]]; then
  echo "[render-analyze] missing current video: $CUR_VIDEO" >&2
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "[render-analyze] ffmpeg is required" >&2
  exit 1
fi

if ! command -v ffprobe >/dev/null 2>&1; then
  echo "[render-analyze] ffprobe is required" >&2
  exit 1
fi

RUN_ID="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="$OUT_ROOT/$RUN_ID"
mkdir -p "$OUT_DIR"/{ref_frames,cur_frames}
mkdir -p "$OUT_DIR/worst_frames"

echo "[render-analyze] run_id=$RUN_ID"
echo "[render-analyze] out_dir=$OUT_DIR"

REF_NORM="$OUT_DIR/ref_norm.mp4"
CUR_NORM="$OUT_DIR/cur_norm.mp4"

ffmpeg -y -i "$REF_VIDEO" \
  -vf "fps=$FPS,scale=${SIZE}:${SIZE}:force_original_aspect_ratio=increase,crop=${SIZE}:${SIZE}" \
  -an -c:v libx264 -pix_fmt yuv420p "$REF_NORM" >/dev/null 2>&1

ffmpeg -y -i "$CUR_VIDEO" \
  -vf "fps=$FPS,scale=${SIZE}:${SIZE}:force_original_aspect_ratio=increase,crop=${SIZE}:${SIZE}" \
  -an -c:v libx264 -pix_fmt yuv420p "$CUR_NORM" >/dev/null 2>&1

REF_DUR="$(ffprobe -v error -show_entries format=duration -of default=nokey=1:noprint_wrappers=1 "$REF_NORM" | awk '{print int($1)}')"
CUR_DUR="$(ffprobe -v error -show_entries format=duration -of default=nokey=1:noprint_wrappers=1 "$CUR_NORM" | awk '{print int($1)}')"
SHORT_DUR="$REF_DUR"
if (( CUR_DUR < SHORT_DUR )); then
  SHORT_DUR="$CUR_DUR"
fi
if (( SHORT_DUR <= 0 )); then
  SHORT_DUR=1
fi

REF_TRIM="$OUT_DIR/ref_trim.mp4"
CUR_TRIM="$OUT_DIR/cur_trim.mp4"
ffmpeg -y -i "$REF_NORM" -t "$SHORT_DUR" -an -c:v copy "$REF_TRIM" >/dev/null 2>&1
ffmpeg -y -i "$CUR_NORM" -t "$SHORT_DUR" -an -c:v copy "$CUR_TRIM" >/dev/null 2>&1

SSIM_LOG="$OUT_DIR/ssim.log"
PSNR_LOG="$OUT_DIR/psnr.log"

ffmpeg -i "$REF_TRIM" -i "$CUR_TRIM" \
  -lavfi "[0:v][1:v]ssim=stats_file=$SSIM_LOG" \
  -f null - >/dev/null 2>&1

ffmpeg -i "$REF_TRIM" -i "$CUR_TRIM" \
  -lavfi "[0:v][1:v]psnr=stats_file=$PSNR_LOG" \
  -f null - >/dev/null 2>&1

SIDE_BY_SIDE="$OUT_DIR/side_by_side.mp4"
DIFF_VIDEO="$OUT_DIR/diff.mp4"
DIFF_STILL="$OUT_DIR/diff-first.png"

ffmpeg -y -i "$REF_TRIM" -i "$CUR_TRIM" \
  -filter_complex "[0:v][1:v]hstack=inputs=2[v]" \
  -map "[v]" -an -c:v libx264 -pix_fmt yuv420p "$SIDE_BY_SIDE" >/dev/null 2>&1

ffmpeg -y -i "$REF_TRIM" -i "$CUR_TRIM" \
  -filter_complex "[0:v][1:v]blend=all_mode=difference[v]" \
  -map "[v]" -an -c:v libx264 -pix_fmt yuv420p "$DIFF_VIDEO" >/dev/null 2>&1

ffmpeg -y -i "$DIFF_VIDEO" -vf "select=eq(n\\,0)" -vframes 1 "$DIFF_STILL" >/dev/null 2>&1

ffmpeg -y -i "$REF_TRIM" -vf "fps=1" -frames:v "$FRAME_COUNT" "$OUT_DIR/ref_frames/frame-%02d.png" >/dev/null 2>&1
ffmpeg -y -i "$CUR_TRIM" -vf "fps=1" -frames:v "$FRAME_COUNT" "$OUT_DIR/cur_frames/frame-%02d.png" >/dev/null 2>&1

FRAME_METRICS="$OUT_DIR/frame_metrics.tsv"
{
  echo -e "frame\tssim"
  sed -n 's/.*n:\([0-9][0-9]*\).*All:\([0-9.][0-9.]*\).*/\1\t\2/p' "$SSIM_LOG" \
    | awk -F'\t' '{ printf "%s\t%.6f\n", $1, $2 }'
} > "$FRAME_METRICS"

WORST_LIST="$OUT_DIR/worst_frames.tsv"
{
  head -n 1 "$FRAME_METRICS"
  tail -n +2 "$FRAME_METRICS" | sort -k2,2n | head -n "$WORST_FRAMES"
} > "$WORST_LIST"

while IFS=$'\t' read -r frame score; do
  if [[ "$frame" == "frame" || -z "$frame" ]]; then
    continue
  fi

  REF_FRAME="$OUT_DIR/worst_frames/ref-$frame.png"
  CUR_FRAME="$OUT_DIR/worst_frames/cur-$frame.png"
  DIFF_FRAME="$OUT_DIR/worst_frames/diff-$frame.png"

  ffmpeg -y -i "$REF_TRIM" -vf "select=eq(n\\,$frame)" -vframes 1 "$REF_FRAME" >/dev/null 2>&1 || true
  ffmpeg -y -i "$CUR_TRIM" -vf "select=eq(n\\,$frame)" -vframes 1 "$CUR_FRAME" >/dev/null 2>&1 || true

  if [[ -f "$REF_FRAME" && -f "$CUR_FRAME" ]]; then
    ffmpeg -y -i "$REF_FRAME" -i "$CUR_FRAME" \
      -filter_complex "[0:v][1:v]blend=all_mode=difference[v]" -map "[v]" -frames:v 1 "$DIFF_FRAME" >/dev/null 2>&1 || true
  fi
done < "$WORST_LIST"

AVG_SSIM="$(awk -F'All:' '/All:/{split($2,a," "); sum+=a[1]; n++} END{if(n>0) printf "%.6f", sum/n; else print "0"}' "$SSIM_LOG")"
AVG_PSNR="$(awk -F'psnr_avg:' '/psnr_avg:/{split($2,a," "); sum+=a[1]; n++} END{if(n>0) printf "%.3f", sum/n; else print "0"}' "$PSNR_LOG")"

GRADE="A"
NOTE="구조 유사도가 높습니다."
awk_check="$(awk -v s="$AVG_SSIM" 'BEGIN { if (s < 0.72) print "low"; else if (s < 0.84) print "mid"; else print "high"; }')"
if [[ "$awk_check" == "low" ]]; then
  GRADE="C"
  NOTE="레이어 구조 차이가 큽니다. 오브 로직 재구성이 필요합니다."
elif [[ "$awk_check" == "mid" ]]; then
  GRADE="B"
  NOTE="유사하지만 파동/입자 밀도와 위상 동기 튜닝이 필요합니다."
fi

REPORT="$OUT_DIR/report.md"
cat > "$REPORT" <<REPORT_EOF
# Orb Render Match Report

- run_id: $RUN_ID
- reference_video: $REF_VIDEO
- current_video: $CUR_VIDEO
- compare_duration_sec: $SHORT_DUR
- normalized_size: ${SIZE}x${SIZE}
- compare_fps: $FPS
- avg_ssim: $AVG_SSIM
- avg_psnr: $AVG_PSNR
- grade: $GRADE

## Assessment
$NOTE

## Artifacts
- side_by_side: $SIDE_BY_SIDE
- diff_video: $DIFF_VIDEO
- diff_first_frame: $DIFF_STILL
- reference_frames: $OUT_DIR/ref_frames
- current_frames: $OUT_DIR/cur_frames
- frame_metrics: $FRAME_METRICS
- worst_frames_list: $WORST_LIST
- worst_frames_images: $OUT_DIR/worst_frames
- ssim_log: $SSIM_LOG
- psnr_log: $PSNR_LOG
REPORT_EOF

echo "[render-analyze] avg_ssim=$AVG_SSIM avg_psnr=$AVG_PSNR grade=$GRADE"
echo "[render-analyze] report=$REPORT"
echo "[render-analyze] side_by_side=$SIDE_BY_SIDE"
echo "[render-analyze] diff_video=$DIFF_VIDEO"
