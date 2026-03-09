# NanoClaw Memory Guide

이 프로젝트의 런타임 메모리는 Git 추적 파일이 아니라 `shared_data/shared_memory/` 아래에 자동으로 누적됩니다.

## 왜 분리했는가
- 운영 중 생성되는 이벤트/대화 로그는 민감 데이터가 포함될 수 있어 Git 추적에서 제외합니다.
- 코드/문서 변경 이력과 런타임 메모리를 분리해 보안/운영 리스크를 줄입니다.

## 실제 메모리 파일
- 런타임 타임라인: `shared_data/shared_memory/memory.md`
- Minerva working memory: `shared_data/shared_memory/minerva_working_memory.json`
- Clio knowledge memory: `shared_data/shared_memory/clio_knowledge_memory.json`
- Hermes evidence memory: `shared_data/shared_memory/hermes_evidence_memory.json`
- Clio claim review queue: `shared_data/shared_memory/clio_claim_review_queue.json`
- Telegram 대화 로그: `shared_data/shared_memory/telegram_chat_history.json`
- 이벤트 로그: `shared_data/shared_memory/agent_events.json`
- 생성 시점:
  - `/api/orchestration/events` 이벤트 수신 시
  - Telegram 텍스트 대화 히스토리 저장 시
- 보존 정책:
  - `MEMORY_MD_MAX_BYTES`를 초과하면 자동 회전(기본 280000 bytes)
  - `minerva_working_memory.json`은 회전 대상이 아니라 운영자가 갱신하는 구조화 메모리입니다.

## Minerva 메모리 주입 정책
- Minerva 일반 대화에는 raw `memory.md`가 아니라 `minerva_working_memory.json`이 주입됩니다.
- Clio/Hermes 기본 호출에는 각각 `clio_knowledge_memory.json`, `hermes_evidence_memory.json` 기반 compact context가 주입됩니다.
- 목적은 장기 목표, 현재 프로젝트, 의사결정 선호를 짧게 유지하는 것입니다.
- 주입량 제한:
  - `MINERVA_WORKING_MEMORY_MAX_CHARS=1800`
- 포함해야 하는 것:
  - 장기 목표
  - 현재 집중 프로젝트
  - 현재 공백/병목
  - 답변 스타일 선호
- 넣지 않는 것:
  - 취미
  - 테스트/리허설 흔적
  - 과거 프로젝트 세부 이력 전체
  - raw Telegram 대화 전문

## 메모리 노이즈 억제
- 기본 제외 태그: `MEMORY_SKIP_TAGS=verification,rehearsal,test,smoke`
- 제목/토픽/요약에 `verification/rehearsal/smoke-test/healthcheck/heartbeat`가 포함되면 저장 제외
- Hermes 중복 브리핑(출처 없음 + 집계 요약만 존재)은 저장 제외

## 운영 검증 명령
```bash
npm run verify:memory
```

## Clio knowledge review 검증 명령
```bash
npm run verify:clio:knowledge
npm run verify:clio:approval
```

운영 명령
```bash
/clio_reviews
```

- `/clio_reviews`는 승인 대기 중인 Clio knowledge 노트를 Telegram에서 확인합니다.
- 승인 완료 시 해당 노트의 `draft_state`는 `confirmed`로 승격됩니다.

## Hermes 스케줄 브리핑 검증 명령
```bash
npm run verify:hermes:schedule
```
