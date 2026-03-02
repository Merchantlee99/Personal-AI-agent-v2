# NanoClaw v2 Operations Playbook

## 1. 사전 준비
1. `.env.local.example`를 복사해 `.env.local` 생성
2. `INTERNAL_API_TOKEN`, `INTERNAL_SIGNING_SECRET`, `N8N_ENCRYPTION_KEY`를 강한 값으로 교체
  - 필요 시 내부 보호 한도 조정:
    - `INTERNAL_RATE_LIMIT_PER_MINUTE`
    - `INTERNAL_RATE_LIMIT_WINDOW_SEC`
    - `INTERNAL_NONCE_TTL_SEC`
    - `INTERNAL_NONCE_MAX_ENTRIES`
3. 루트 경로에서 Docker/Node 설치 상태 확인
4. 에이전트 ID/role 변경 시 `config/agents.json`만 수정
5. 실제 모델 호출 사용 시 `.env.local`에 `LLM_PROVIDER=gemini`와 `GEMINI_API_KEY`(또는 `GOOGLE_API_KEY`) 설정
6. Telegram orchestration 사용 시 `.env.local`에 아래 항목 설정
  - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_WEBHOOK_SECRET`
  - 정책값: `MINERVA_IMMEDIATE_MIN_PRIORITY`, `MINERVA_IMMEDIATE_MIN_CONFIDENCE`, `MINERVA_TOPIC_COOLDOWN_HOURS`, `MINERVA_DIGEST_SLOTS`

## 2. 기동 절차
1. `docker compose build`
2. `docker compose up -d`
3. `docker compose ps`

### n8n 로컬 워크플로 부트스트랩
`n8n/workflows/nanoclaw-v2-smoke.json`를 기준으로 자동 등록/활성화한다.

1. `npm run n8n:bootstrap`
2. 성공 기준: `webhook is ready (200)` 출력
3. 검증 요청: `POST http://localhost:5678/webhook/nanoclaw-v2-smoke`

참고:
- `N8N_ENCRYPTION_KEY`를 교체하면 기존 `n8n_data`의 설정 키와 불일치가 날 수 있다.
- 이 경우 기존 n8n 볼륨의 워크플로가 보존되지 않으므로 bootstrap을 재실행해야 한다.
- 워크플로 JSON 변경을 강제로 반영하려면 `N8N_BOOTSTRAP_FORCE_IMPORT=true`를 사용한다.
- `n8n` 포트는 기본 `127.0.0.1:5678` 로컬 바인딩이다.
- n8n v1+에서는 `N8N_BASIC_AUTH_*`가 공식적으로 제거되었으므로 외부 공개 시 ingress/reverse-proxy 인증을 추가한다.
- 중복 워크플로가 생기면 `npm run n8n:cleanup`으로 단일 활성 상태를 강제한다.
  - 현재 n8n CLI에는 `delete:workflow`가 없어서 중복 레코드는 비활성화로 정리한다.
- 중복 레코드를 물리적으로 1개로 정리하려면 `npm run n8n:reset-singleton` 실행
  - 실행 전 자동 백업 경로: `shared_data/workflows/backups/n8n-reset-<timestamp>`

### Hermes 실사용 워크플로 검증
`n8n/workflows/hermes-daily-briefing.json`를 기준으로 브리핑 템플릿/중복 억제를 검증한다.

1. `bash scripts/n8n/bootstrap-hermes-daily-briefing.sh`
2. `bash scripts/n8n/test-hermes-daily-briefing-workflow.sh`
3. 성공 기준:
  - 1차 호출: `skipped=false`, `briefing_markdown` 포함
  - 2차 동일 호출: `skipped=true`, `reason=duplicate_briefing`
4. 기본 정책: 기존 Hermes workflow 재사용(중복 누적 방지)
  - workflow JSON 변경을 강제로 반영할 때만 `N8N_HERMES_FORCE_IMPORT=true bash scripts/n8n/bootstrap-hermes-daily-briefing.sh`
5. Minerva 오케스트레이션 연동 검증(선택)
  - `HERMES_DISPATCH_TO_MINERVA=true FRONTEND_PORT=3000 bash scripts/n8n/test-hermes-daily-briefing-workflow.sh`
  - 내부적으로 `scripts/n8n/dispatch-hermes-briefing-to-minerva.sh`를 통해 `/api/orchestration/events`로 이벤트 publish

## 3. 런타임 검증
1. llm-proxy health
- `curl http://localhost:8001/health`

2. frontend `/api/chat` 왕복
- Next.js 실행 후 `POST /api/chat` 호출
- canonical id, model, reply가 응답에 포함되는지 확인
- 고정 요청 계약(JSON):
  - `agentId: "minerva" | "clio" | "hermes"` (canonical only)
  - `message: string` (필수)
  - `history?: [{ role: string, text?: string, content?: string, at?: string }]`
  - `history[].text`를 기본으로 사용하고, legacy `history[].content`는 호환 입력으로만 허용
- 고정 응답 계약(JSON):
  - `agentId: "minerva" | "clio" | "hermes"`
  - `model: string`
  - `reply: string`

3. n8n webhook 확인
- `POST http://localhost:5678/webhook/<path>` (활성 workflow 기준)
- `webhook-test`는 캔버스에서 Execute workflow를 누른 직후 1회만 유효

4. agent 파일 감시 확인
- `shared_data/inbox`에 JSON 파일 투입
- `shared_data/obsidian_vault`와 `shared_data/outbox`, `shared_data/archive` 생성 확인
- Clio(`agent_id=clio`) 투입 시 `shared_data/verified_inbox`에 JSON 생성 확인
  - 핵심 필드: `tags`, `related_links`, `notebooklm.ready`, `notebooklm.vault_file`

5. Minerva orchestration 이벤트 확인
- `POST /api/orchestration/events`로 Hermes/Clio 이벤트 입력
- 응답에서 `decision` 확인
  - `send_now`: Telegram 즉시 발송 시도
  - `queue_digest`: digest 큐 적재
  - `suppressed_cooldown`: 동일 토픽 쿨다운 억제

6. Telegram inline callback 확인
- `POST /api/telegram/webhook` (Telegram callback update 형식)
- 액션별 기대 결과:
  - `clio_save:event_id` -> `shared_data/inbox`에 clio task 생성
  - `hermes_deep_dive:event_id` -> `shared_data/inbox`에 hermes deep-dive task 생성
  - `mute_topic:event_id` -> `shared_data/shared_memory/topic_cooldowns.json` 갱신

### 통합 스모크 검증(권장)
- `npm run verify:smoke`
- 실행 항목:
  - docker 서비스 기동 확인
  - llm-proxy `/health`
  - n8n webhook 200
  - `/api/chat` canonical/legacy-history 호환
  - `/api/chat` legacy alias 거부(400)
  - agent watchdog 처리
  - 보안 옵션(read_only/cap_drop/no-new-privileges/network) 점검

### 오케스트레이션 흐름 검증
- Next.js dev 실행 상태에서 `npm run verify:orchestration`
- 검증 항목:
  - 이벤트 수집(`/api/orchestration/events`)
  - Telegram callback 액션(`/api/telegram/webhook`)
  - `clio_save` 액션의 inbox task 생성

### 회귀 테스트
- `npm run test:proxy`
- 검증 항목:
  - canonical 정규화 + legacy alias 거부
  - history `text`/`content` 호환
  - llm-proxy 모델 호출 retry/fallback 정책

### LLM 비용/쿼터 모니터링
- `npm run verify:llm-usage`
- 기본 집계 범위: 최근 24시간(`LLM_USAGE_SINCE=24h`)
- 출력 항목: `/api/agent` 총 호출수, 성공수, 5xx, quota 429 이벤트
- 경보 임계값: `LLM_ALERT_429_THRESHOLD` (기본 0)
- 엄격 모드 실패 처리: `LLM_ALERT_STRICT=true`
- 우선 데이터 소스: `shared_data/logs/llm_usage_metrics.json` (llm-proxy 누적 메트릭 저장)
- metrics 파일이 없으면 컨테이너 로그 파싱으로 fallback

### CI 자동 검증
- 워크플로: [`.github/workflows/runtime-verification.yml`](../.github/workflows/runtime-verification.yml)
- 트리거: `main`, `codex/**`, PR, 수동 실행
- 실행 순서:
  1. `npm ci`
  2. CI용 `.env.local` 생성(랜덤 키 주입)
  3. `npm run test:proxy`
  4. `npm run verify:smoke`

## 4. 장애 대응 표준
- 보고 형식: `원인 1줄 + 대안 1~2줄 + 다음 액션 1줄`
- 우선순위:
  1. llm-proxy 인증 오류
  2. n8n webhook 장애
  3. 파일 감시/문서 출력 실패

## 5. 보안 체크리스트
- [ ] `read_only`, `cap_drop`, `no-new-privileges` 활성
- [ ] internal/external 네트워크 분리 적용
- [ ] `.env.local` 비커밋 상태
- [ ] canonical id(`minerva/clio/hermes`)만 허용되는지 확인
- [ ] unknown agent가 minerva fallback 되는지 확인

## 6. 브랜치 보호 적용
GitHub API 토큰이 준비되면 아래 명령으로 `main` 보호 규칙을 적용한다.

```bash
GITHUB_TOKEN=*** \
GITHUB_REPO=Merchantlee99/Personal-AI-agent-v2 \
GITHUB_BRANCH=main \
npm run security:branch-protect
```

기본 적용값:
- required status check: `runtime-verification`
- PR 1명 승인 필수 + stale review dismiss
- direct force push/delete 금지
- linear history + conversation resolution 필수

운영 프로필:
- `PROTECTION_PROFILE=strict` (기본): 승인 1명 필수
- `PROTECTION_PROFILE=solo`: 승인 0명(1인 운영용), 나머지 보호 규칙은 유지
- `PROTECTION_PROFILE=auto`: 토큰 사용자와 저장소 owner가 같으면 승인 0명, 아니면 1명

1인 운영 적용 예시:
```bash
PROTECTION_PROFILE=solo \
GITHUB_TOKEN=*** \
GITHUB_REPO=Merchantlee99/Personal-AI-agent-v2 \
GITHUB_BRANCH=main \
npm run security:branch-protect
```

운영자 수 기준 자동 적용(권장):
- push 권한 운영자 수가 `2` 이상이면 `strict`, 미만이면 `solo`를 자동 선택한다.
```bash
GITHUB_TOKEN=*** \
GITHUB_REPO=Merchantlee99/Personal-AI-agent-v2 \
GITHUB_BRANCH=main \
npm run security:branch-protect:auto
```

문제 해결(401/403/404):
- `401 Bad credentials`는 기존 보호 규칙 충돌이 아니라 토큰 인증 실패다.
- `403`은 토큰은 유효하지만 repo admin 권한 부족 또는 SSO 승인 누락일 가능성이 높다.
- `404`는 `GITHUB_REPO`/`GITHUB_BRANCH` 값 오류 또는 저장소 접근권한 문제일 수 있다.

사전 점검 명령:
```bash
curl -sS -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/user | jq '{login,id}'
curl -sS -H "Authorization: Bearer $GITHUB_TOKEN" "https://api.github.com/repos/$GITHUB_REPO" | jq '.permissions'
curl -sS -H "Authorization: Bearer $GITHUB_TOKEN" "https://api.github.com/repos/$GITHUB_REPO/branches/$GITHUB_BRANCH/protection" | jq '.required_status_checks.contexts'
```
