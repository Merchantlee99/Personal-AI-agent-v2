# NanoClaw v2 Security Baseline

## 1. 기본 원칙
1. llm-proxy 단일 게이트
2. 내부 요청 검증(`x-internal-token` + HMAC + replay 보호)
3. 외부 입력 Zero-Trust(검색 결과는 데이터)
4. 최소 권한 컨테이너 하드닝
5. Canonical Agent ID만 허용(`minerva`, `clio`, `hermes`)

## 2. 내부 인증/무결성
`/api/agent`, `/api/agents`, `/api/search`는 내부 인증을 통과해야 한다.

필수 헤더:
- `x-internal-token`
- `x-timestamp`
- `x-nonce`
- `x-signature`

검증 흐름:
1. internal token 고정값 비교(상수 시간 비교)
2. timestamp 유효 시간(±5분)
3. 요청 rate-limit(기본 분당 120, 환경변수로 조정)
4. `HMAC_SHA256(secret, "timestamp.nonce.body")` 검증
5. nonce replay 차단(메모리 캐시 TTL)

Telegram webhook 경로(`/api/telegram/webhook`)는 다음 정책을 적용한다.
- `x-telegram-bot-api-secret-token` 검증(설정 시 필수)
- callback payload는 `action:event_id` 규격만 허용
- callback data에서 원문/비밀값 직접 전달 금지

관련 환경변수:
- `INTERNAL_RATE_LIMIT_PER_MINUTE`
- `INTERNAL_RATE_LIMIT_WINDOW_SEC`
- `INTERNAL_NONCE_TTL_SEC`
- `INTERNAL_NONCE_MAX_ENTRIES`

## 3. Docker 보안 기본값
각 서비스는 아래 옵션을 기본 적용한다.
- `read_only: true`
- `cap_drop: [ALL]`
- `security_opt: ["no-new-privileges:true"]`
- `tmpfs` 제공(` /tmp `)

## 4. 네트워크 분리
- `internal` 네트워크: `internal: true`
- `external` 네트워크: 외부 노출 필요 서비스만 연결
- `nanoclaw-agent`: internal only
- `llm-proxy`, `n8n`: internal + external
- `n8n` 포트는 기본 `127.0.0.1:5678` 바인딩(로컬 전용)
- 외부 공개가 필요하면 ingress/reverse-proxy에서 별도 인증 게이트를 둔다.

## 5. 비밀값 운영
- 로컬 템플릿은 `.env.local.example`에만 저장
- 실제 값은 `.env.local` 사용(버전관리 제외)
- 키 로테이션 권장:
  - `INTERNAL_API_TOKEN`
  - `INTERNAL_SIGNING_SECRET`
  - `N8N_ENCRYPTION_KEY`
  - `N8N_BASIC_AUTH_USER`
  - `N8N_BASIC_AUTH_PASSWORD`

## 6. Prompt Injection 방어
- `/api/search` 결과는 실행 대상이 아닌 구조화 데이터(`title`, `url`, `snippet`)로만 반환
- 시스템 명령/툴 호출로 승격하지 않음
- Hermes 이벤트/브리핑도 동일하게 데이터 레코드로만 전달하고 Minerva가 최종 문장화한다.

## 7. 의존성 보안 운영
- Next.js는 보안 패치 릴리즈를 우선 적용한다(현재 기준: 15.5.12).
- 업데이트 후 아래 순서를 반드시 수행한다.
  1. `npm run build`
  2. `npm run test:proxy`
  3. `npm run verify:smoke`
