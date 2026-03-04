# NanoClaw v2 Security Baseline

이 문서는 "어떤 위협을 어떤 통제로 막는지"를 구현 근거와 함께 설명합니다.

## 1) 보호 목표
- 내부 API 위조/변조/재전송(replay) 방지
- Telegram webhook/callback 오용 차단
- Hermes 수집 경로의 prompt injection/unsafe URL 차단
- 컨테이너 과권한 축소 및 네트워크 경계 유지

## 2) Trust Boundary

```mermaid
flowchart LR
  EXT["Untrusted Input\n(Web, Telegram, n8n, External Search)"] --> API["Next.js API"]
  API --> PX["llm-proxy"]
  API --> ORCH["/api/orchestration/events"]
  API --> TGCB["/api/telegram/webhook"]
  TGCB --> INBOX["shared_data/inbox"]
  ORCH --> MEM["shared_data/shared_memory"]
  INBOX --> AG["nanoclaw-agent"]

  classDef trusted fill:#e8f1ff,stroke:#2563eb,stroke-width:1px;
  class API,PX,ORCH,TGCB,AG trusted;
```

원칙
- 외부 입력은 "실행 지시"가 아닌 "데이터"로만 취급합니다.

## 3) 내부 요청 인증/무결성 체인

적용 대상
- `llm-proxy`: `/api/agent`, `/api/agents`, `/api/search`

필수 헤더
- `x-internal-token`
- `x-timestamp`
- `x-nonce`
- `x-signature`

검증 순서
1. token 검증
2. rate-limit 검증
3. timestamp 검증
4. HMAC signature 검증
5. nonce 저장/재사용 차단

```mermaid
sequenceDiagram
  participant C as Caller(Next.js)
  participant P as llm-proxy security

  C->>P: token + timestamp + nonce + signature + body
  P->>P: token verify
  P->>P: fixed-window rate limit
  P->>P: timestamp window check
  P->>P: HMAC verify
  P->>P: nonce store + replay reject
  P-->>C: allow / reject
```

이 순서의 이유
- 서명 검증 전에 nonce를 저장하면, 무효 요청으로 nonce cache를 오염시켜 DoS 표면을 키울 수 있습니다.

## 4) Telegram 보안 통제

통제 항목
- webhook secret: `TELEGRAM_WEBHOOK_SECRET`
- 호출자 allowlist
  - `TELEGRAM_ALLOWED_USER_IDS`
  - `TELEGRAM_ALLOWED_CHAT_IDS`
- callback action allowlist
  - `TELEGRAM_ALLOWED_CALLBACK_ACTIONS`
- 일반 텍스트 대화 rate-limit
  - `TELEGRAM_TEXT_RATE_LIMIT_WINDOW_SEC`
  - `TELEGRAM_TEXT_RATE_LIMIT_MAX`

허용 액션 표준
- `clio_save`
- `hermes_deep_dive`
- `minerva_insight`

## 5) Hermes 수집 보안 통제

n8n/프록시 공통 원칙
- prompt-like 패턴 제거(`INJECTION_PATTERNS`)
- unsafe URL 차단(`localhost`, private/loopback/link-local)
- 계약: `inert_search_records_only`
- 통계: `security_stats` 유지

적용 파일
- `n8n/workflows/hermes-daily-briefing.json`
- `n8n/workflows/hermes-web-search-tavily.json`
- `proxy/app/search_client.py`

## 6) 런타임 하드닝

공통 하드닝(`docker-compose.yml`)
- `read_only: true`
- `cap_drop: [ALL]`
- `security_opt: ["no-new-privileges:true"]`
- `tmpfs` 사용

네트워크
- `internal`: 내부 통신 전용
- `external`: 외부 API 필요한 서비스만 연결

## 7) 위협-통제 매트릭스

| 위협 | 통제 | 구현 근거 |
|---|---|---|
| 내부 요청 위조 | token + HMAC + timestamp + nonce | `proxy/app/security.py` |
| replay 공격 | nonce TTL + 재사용 차단 | `ReplayWindow` (`proxy/app/security.py`) |
| Telegram 오용 | secret + allowlist + action allowlist | `src/app/api/telegram/webhook/route.ts` |
| prompt injection | 패턴 제거 + inert data contract | `n8n/workflows/*.json`, `proxy/app/search_client.py` |
| unsafe URL/내부망 유도 | public URL 검증 | n8n code nodes + `search_client.py` |
| 과권한 컨테이너 | read_only/cap_drop/no-new-privileges | `docker-compose.yml` |

## 8) 비밀값 운영 규칙
- 실제 비밀은 `.env.local`에만 저장(커밋 금지)
- 우선 로테이션 대상
  - `INTERNAL_API_TOKEN`
  - `INTERNAL_SIGNING_SECRET`
  - `N8N_ENCRYPTION_KEY`
  - `TELEGRAM_WEBHOOK_SECRET`
  - `GOOGLE_CALENDAR_OAUTH_CLIENT_SECRET`
  - `DEEPL_API_KEY`

## 9) 최소 보안 검증 명령

```bash
npm run security:check-orchestration
npm run verify:smoke
npm run verify:telegram:inline
npm run verify:clio-e2e
npm run test:proxy
```

## 10) 아직 남아있는 보안 과제
- Human-in-the-loop 승인 큐(고위험 액션 이중확인)
- 이벤트 스키마 버전 강제(JSON Schema + backward compatibility)
- 단일 관측 API(`/api/runtime-metrics`) 기반 경보 자동화

위 3개는 구조 보안을 더 높이는 다음 단계이며, 현재 상태는 "기본 통제는 완료, 고급 운영 통제는 진행 중"입니다.
