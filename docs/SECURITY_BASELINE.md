# NanoClaw v2 Security Baseline

이 문서는 현재 운영형 NanoClaw가 어떤 위협을 어떤 통제로 막는지 정리합니다.

## 1) 보호 목표
- 내부 API 위조/변조/재전송(replay) 방지
- Telegram polling bridge/callback 오용 차단
- Hermes 수집 경로의 prompt injection/unsafe URL 차단
- 컨테이너 과권한 축소 및 네트워크 경계 유지
- host secret의 불필요한 평문 노출 축소
- user-facing vault와 runtime/internal 데이터 경계 유지

## 2) Trust Boundary

```mermaid
flowchart LR
  EXT["Untrusted Input\n(Telegram, n8n, External Search, OAuth callback)"] --> PX["llm-proxy FastAPI"]
  PX --> INBOX["shared_data/inbox"]
  PX --> MEM["shared_data/shared_memory"]
  INBOX --> AG["nanoclaw-agent"]
  AG --> VAULT["shared_data/obsidian_vault"]
  AG --> RUNTIME["shared_data/runtime_agent_notes"]

  classDef trusted fill:#e8f1ff,stroke:#2563eb,stroke-width:1px;
  class PX,AG trusted;
```

원칙
- 외부 입력은 실행 지시가 아닌 데이터로만 취급합니다.
- user-facing vault에는 Clio note만 씁니다.
- Minerva/Hermes runtime markdown, queue/raw state, support file은 vault 밖에 둡니다.

## 3) 내부 요청 인증/무결성 체인

적용 대상
- `llm-proxy`: `/api/agent`, `/api/agents`, `/api/search`, Google Calendar 내부 조회 엔드포인트

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

이 순서의 이유
- 서명 검증 전에 nonce를 저장하면 무효 요청으로 nonce cache를 오염시켜 DoS 표면이 커집니다.

## 4) Telegram 보안 통제
- bridge secret: `TELEGRAM_WEBHOOK_SECRET`
- 호출자 allowlist
  - `TELEGRAM_ALLOWED_USER_IDS`
  - `TELEGRAM_ALLOWED_CHAT_IDS`
- callback action allowlist
  - `TELEGRAM_ALLOWED_CALLBACK_ACTIONS`
- 일반 텍스트 대화 rate-limit
- 승인 큐 2단계 확인 + TTL

기본 수신 방식
- `telegram-poller`의 outbound polling
- 공개 Telegram webhook URL은 기본 운영 경로가 아님
- `429/일시적 오류`는 재시도, 영구 `4xx`는 dead-letter 저장

## 5) Hermes 수집 보안 통제
- prompt-like 패턴 제거
- unsafe URL 차단(`localhost`, private/loopback/link-local)
- `inert_search_records_only` 계약 유지
- Tavily API base는 `https + allowlisted host`만 허용

적용 파일
- `n8n/workflows/hermes-daily-briefing.json`
- `n8n/workflows/hermes-web-search-tavily.json`
- `proxy/app/search_client.py`

## 6) Google Calendar read-only 통제
- OAuth scope 고정: `https://www.googleapis.com/auth/calendar.readonly`
- Telegram 명령(`/gcal_connect`, `/gcal_status`, `/gcal_today`)만 운영 플로우로 사용
- 브리핑 자동 첨부는 `GOOGLE_CALENDAR_ATTACH_TO_MORNING_BRIEFING`로 제어

## 7) 런타임 하드닝
공통 하드닝(`docker-compose.yml`)
- `read_only: true`
- `cap_drop: [ALL]`
- `security_opt: ["no-new-privileges:true"]`
- `tmpfs` 사용

네트워크
- `internal`: 내부 통신 전용
- `external`: 외부 API 필요한 서비스만 연결

## 8) 비밀값 운영 규칙
- `.env.local`에는 실제 값 대신 ref를 둘 수 있습니다.
- 실제 값은 macOS Keychain 또는 1Password에 저장합니다.
- compose 실행은 `bash scripts/runtime/compose.sh ...`로 고정합니다.
- 서비스별 화이트리스트 키만 컨테이너에 전달합니다.

우선 로테이션 대상
- `INTERNAL_API_TOKEN`
- `INTERNAL_SIGNING_SECRET`
- `TELEGRAM_WEBHOOK_SECRET`
- `GOOGLE_CALENDAR_OAUTH_CLIENT_SECRET`
- `DEEPL_API_KEY`
- LLM provider API keys

## 9) 위협-통제 매트릭스

| 위협 | 통제 | 구현 근거 |
|---|---|---|
| 내부 요청 위조 | token + HMAC + timestamp + nonce | `proxy/app/security.py` |
| replay 공격 | nonce TTL + 재사용 차단 | `proxy/app/security.py` |
| Telegram 오용 | secret + allowlist + action allowlist + approval queue | `proxy/app/main.py`, `proxy/app/telegram_bridge.py` |
| poller 유실 은닉 | dead-letter 기록 + offset 분리 | `proxy/app/telegram_poller.py` |
| prompt injection | 패턴 제거 + inert data contract | `n8n/workflows/*.json`, `proxy/app/search_client.py` |
| unsafe URL/내부망 유도 | public URL 검증 | `proxy/app/search_client.py`, n8n code nodes |
| Tavily API base 오염 | https + allowlisted host 강제 | `proxy/app/search_client.py` |
| 과권한 컨테이너 | read_only/cap_drop/no-new-privileges | `docker-compose.yml` |
| user vault 오염 | Clio-only write + runtime/support 분리 | `agent/main.py`, `shared_data/*` |
| host secret 평문 노출 | Keychain/1Password ref + compose wrapper | `scripts/runtime/*.sh` |

## 10) 현재 남은 보안 리스크
1. polling dead-letter는 완화됐지만 운영상 메시지 유실을 100% 없앤 것은 아님
2. NotebookLM은 아직 비활성 운영 상태
3. Aegis는 아직 계획 단계라 동적 격리/control plane은 미도입

## 11) 최소 보안 검증 명령
```bash
npm run security:check-orchestration
npm run verify:smoke
npm run verify:runtime:drift
npm run verify:telegram:inline
npm run verify:morning:preflight
npm run test:proxy
```
