# Orb Render Tuning Playbook

레퍼런스 오브 매칭 목표를 위해 다음 3단계 루프를 고정합니다.

## 1) 실화면 캡처 준비

- dev 서버 실행:
  - `npm run dev -- --hostname 127.0.0.1 --port 3000`
- 브라우저에서 UI 확인:
  - `http://127.0.0.1:3000`
- 화면 녹화:
  - macOS 기본 화면 기록으로 `.mov` 저장
  - 권장 길이: 8~12초
  - 권장 프레이밍: 오브 중심부가 화면 중앙에 오도록 고정

## 2) 프레임 단위 비교 분석

### A. 레퍼런스 vs 현재 결과 비교

```bash
REFERENCE_VIDEO="/abs/path/reference.mov" \
CURRENT_VIDEO="/abs/path/current.mov" \
npm run render:analyze
```

Desktop에서 방금 녹화한 최신 2개 파일을 자동 비교하려면:

```bash
npm run render:analyze:latest
```

산출물 위치:
- `shared_data/render_review/<run_id>/report.md`
- `side_by_side.mp4` (좌: 레퍼런스 / 우: 현재)
- `diff.mp4` (차이 강조)
- `frame_metrics.tsv` (프레임별 SSIM)
- `worst_frames.tsv` + `worst_frames/*.png` (가장 다른 프레임 자동 추출)

### B. 단일 영상 프레임 추출

```bash
INPUT_VIDEO="/abs/path/current.mov" npm run render:extract
```

산출물 위치:
- `shared_data/render_review/frames/<run_id>/frame-00001.png ...`

## 3) 코드 튜닝 규칙

- 우선순위 1: `worst_frames.tsv`의 하위 SSIM 프레임부터 수정
- 우선순위 2: `side_by_side.mp4`로 아래 3요소를 확인
  - 중심 입자 군집의 수축/팽창 위상
  - 외부 원 형상 입자의 무질서 회전 질감
  - 외곽 파동 레이어의 진폭/주기 자연스러움
- 튜닝 후 반드시 같은 입력 영상 길이/구도로 재캡처해서 다시 `render:analyze` 실행

## 추천 반복 루프 (20~30분 단위)

1. 코드 수정 (`src/components/chat-dashboard/orb.tsx`)
2. 브라우저 녹화 8~12초
3. `npm run render:analyze`
4. `worst_frames` 기준으로 다음 수정 포인트 확정

## 자주 쓰는 예시 (현재 Desktop 녹화 파일)

```bash
REFERENCE_VIDEO="/Users/isanginn/Desktop/화면 기록 2026-03-05 오후 6.26.26.mov" \
CURRENT_VIDEO="/Users/isanginn/Desktop/화면 기록 2026-03-05 오후 6.44.28.mov" \
npm run render:analyze
```
