# NanoClaw v2

NanoClaw v2는 `minerva`, `clio`, `hermes` 3개 에이전트를 역할 분리해 운영하는 로컬 우선 오케스트레이션 시스템입니다.

핵심 원칙
- Canonical Agent ID 고정: `minerva`, `clio`, `hermes`
- 모델 호출 단일 게이트: Next.js -> `llm-proxy`
- 외부 수집 결과 Zero-Trust: 명령이 아닌 데이터로만 처리
- 최소 권한 런타임: `read_only`, `cap_drop: [ALL]`, `no-new-privileges`

## 한눈에 보기
```mermaid
flowchart LR
  U[User] --> FE[Next.js UI/API]
  FE --> PX[llm-proxy]
  PX --> LLM[LLM Providers]

  N8N[n8n schedule/webhook] --> ORCH[/api/orchestration/events]
  ORCH --> TG[Telegram]
  TG --> TGC[/api/telegram/webhook]
  TGC --> INBOX[shared_data/inbox]
  INBOX --> AG[nanoclaw-agent]
  AG --> VAULT[obsidian_vault / outbox / verified_inbox]
```

## 빠른 시작
```bash
docker compose build
docker compose up -d
npm run dev -- --hostname 127.0.0.1 --port 3000
```

엔드포인트
- Frontend: `http://127.0.0.1:3000`
- llm-proxy health: `http://127.0.0.1:8001/health`
- n8n: `http://127.0.0.1:5678`

## 문서 지도
- 구조를 이해하려면: `docs/ARCHITECTURE.md`
- 보안을 점검하려면: `docs/SECURITY_BASELINE.md`
- 운영 절차를 실행하려면: `docs/OPERATIONS_PLAYBOOK.md`
- 실제 사용 시나리오를 보려면: `docs/USE_CASES.md`
- Hermes 수집 우선순위/텔레그램 포맷: `docs/HERMES_SOURCE_PRIORITY.md`

## 최신 반영 포인트
- Telegram 인라인 버튼
  - `Clio, 옵시디언에 저장해`
  - `Hermes, 더 찾아`
  - `Minerva, 인사이트 분석해`
- Hermes 딥다이브 후 Minerva 후속 분석 자동 생성 옵션
  - `HERMES_DEEP_DIVE_AUTO_MINERVA=true`
- DeepL 번역 절감 정책
  - `P0`: summary + 상위 2개 snippet
  - `P1`: summary + 상위 1개 snippet
  - `P2`: 자동 번역 없음

## 기본 검증
```bash
npm run verify:smoke
npm run verify:orchestration
npm run verify:telegram:inline
npm run security:check-orchestration
```
