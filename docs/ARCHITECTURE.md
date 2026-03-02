# NanoClaw v2 Architecture

## 1. 목표 구조
NanoClaw v2는 Canonical Agent ID(`minerva`, `clio`, `hermes`)를 기준으로 책임을 분리하고,
프론트엔드 요청을 `llm-proxy` 단일 게이트로 통과시킨 뒤 내부 워커(`nanoclaw-agent`)와 n8n 자동화를 결합하는 구조다.

## 2. 서비스 구성
1. Next.js App Router
- UI: 3-agent 탭 전환 + 에이전트별 히스토리 분리
- API: `/api/chat`는 직접 모델 호출 없이 `llm-proxy /api/agent`로 프록시

2. llm-proxy (FastAPI)
- `/api/agent`: 에이전트 라우팅 및 모델 선택
- `/api/agents`: canonical id 맵 조회(alias 비활성)
- `/api/search`: 외부 검색 결과를 비실행 데이터 레코드로 반환
- `/health`: 헬스 체크

3. nanoclaw-agent (watchdog)
- `shared_data/inbox` 파일 생성 이벤트 감시
- canonical id 검증 + unknown fallback(minerva)
- 결과를 `shared_data/obsidian_vault` 마크다운으로 기록
- 메타데이터를 `shared_data/outbox`로 출력
- Clio 라우팅 시 `shared_data/verified_inbox`에 NotebookLM 준비 JSON(태그/링크/요약) 동시 생성

4. n8n
- webhook 및 스케줄 자동화 허브
- 외부 수집/브리핑 워크플로의 실행 경계

## 3. ID/역할 규칙
- 단일 정의 파일: `config/agents.json`
  - frontend(`src/lib/agents.ts`), llm-proxy(`proxy/app/agents.py`), nanoclaw-agent(`agent/main.py`)가 공통 참조
- Canonical ID: `minerva`, `clio`, `hermes`만 런타임 라우팅 키로 사용
- Alias 비활성: 구 ID(`ace`,`owl`,`dolphin`)는 허용하지 않음
- 역할 경계:
  - Minerva: 오케스트레이션/우선순위/결정
  - Clio: 문서화/지식정리/NotebookLM 준비
  - Hermes: 웹수집/트렌드/브리핑

## 4. 데이터 경로
1. 사용자 -> Next.js `/api/chat`
2. `/api/chat` -> 서명/HMAC 헤더 첨부 -> `llm-proxy /api/agent`
3. `llm-proxy`가 canonical ID로 라우팅 후 응답 반환
4. 파일 기반 작업은 `shared_data/inbox` -> `nanoclaw-agent` -> `obsidian_vault/outbox/archive`
5. Clio 문서 파이프라인은 `shared_data/inbox` -> `nanoclaw-agent` -> `verified_inbox`로 태그/링크 자동화 산출물을 전달

## 5. shared_data 기본 디렉토리
- `inbox/`
- `outbox/`
- `archive/`
- `logs/`
- `verified_inbox/`
- `obsidian_vault/`
- `shared_memory/`
- `queue/`
- `workflows/` (n8n import source for local bootstrap)
