# NanoClaw Memory Guide

이 프로젝트의 런타임 메모리는 Git 추적 파일이 아니라 `shared_data/shared_memory/memory.md`에 자동으로 누적됩니다.

## 왜 분리했는가
- 운영 중 생성되는 이벤트/대화 로그는 민감 데이터가 포함될 수 있어 Git 추적에서 제외합니다.
- 코드/문서 변경 이력과 런타임 메모리를 분리해 보안/운영 리스크를 줄입니다.

## 실제 메모리 파일
- 경로: `shared_data/shared_memory/memory.md`
- 에이전트별 경로:
  - `shared_data/shared_memory/agent_memory/minerva.md`
  - `shared_data/shared_memory/agent_memory/clio.md`
  - `shared_data/shared_memory/agent_memory/hermes.md`
- 생성 시점:
  - `/api/orchestration/events` 이벤트 수신 시
  - Telegram 텍스트 대화 히스토리 저장 시
- 보존 정책:
  - `MEMORY_MD_MAX_BYTES`를 초과하면 자동 회전(기본 280000 bytes)
  - `AGENT_MEMORY_MD_MAX_BYTES`를 초과하면 agent_memory 파일도 자동 회전

## API 비용 절감 기본 정책
- 전체 memory를 매번 넣지 않고, 최근 블록만 압축해 사용
- 기본 주입 대상: `minerva`만 (`CHAT_MEMORY_CONTEXT_AGENTS=minerva`)
- 주입량 제한:
  - `CHAT_MEMORY_CONTEXT_MAX_CHARS=900`
  - `CHAT_MEMORY_CONTEXT_MAX_BLOCKS=4`
- 비활성화: `CHAT_MEMORY_CONTEXT_ENABLED=false`

## 메모리 노이즈 억제
- 기본 제외 태그: `MEMORY_SKIP_TAGS=verification,rehearsal,test,smoke`
- 제목/토픽/요약에 `verification/rehearsal/smoke-test/healthcheck/heartbeat`가 포함되면 저장 제외
- Hermes 중복 브리핑(출처 없음 + 집계 요약만 존재)은 저장 제외

## 운영 검증 명령
```bash
npm run verify:memory
```

## Hermes 스케줄 브리핑 검증 명령
```bash
npm run verify:hermes:schedule
```
