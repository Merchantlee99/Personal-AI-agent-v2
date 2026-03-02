# NanoClaw v2

NanoClaw v2는 `minerva`, `clio`, `hermes` 3개 Canonical Agent ID를 기준으로 역할 경계를 분리하고,  
`llm-proxy` 단일 게이트 + 내부 서명 검증(HMAC) + 최소 권한 컨테이너 운영을 기본값으로 둔 구조입니다.

## Core Principles
- Canonical Agent ID only: `minerva`, `clio`, `hermes`
- Single LLM gateway: Next.js -> `/api/chat` -> `llm-proxy`
- Zero-trust external data: 검색 결과는 실행하지 않고 데이터로만 처리
- Least-privilege runtime: `read_only`, `cap_drop: ALL`, `no-new-privileges`, network split

## Documentation Index
- Commit baseline (필독): [`docs/COMMIT_BASELINE_V2.md`](docs/COMMIT_BASELINE_V2.md)
- Architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Security baseline: [`docs/SECURITY_BASELINE.md`](docs/SECURITY_BASELINE.md)
- Operations playbook: [`docs/OPERATIONS_PLAYBOOK.md`](docs/OPERATIONS_PLAYBOOK.md)
- Rebuild plan: [`docs/PLAN_V2.md`](docs/PLAN_V2.md)

## Quick Start
```bash
docker compose build
docker compose up -d
docker compose ps
```

## Verification
```bash
npm run test:proxy
npm run verify:smoke
npm run verify:llm-usage
```

## Service Endpoints
- Frontend: `http://localhost:3000`
- llm-proxy health: `http://localhost:8001/health`
- n8n: `http://localhost:5678`
