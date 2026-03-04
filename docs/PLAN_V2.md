# NanoClaw Agent System v2 기획서 (보완안)

## 1) 목적
- 기존 NanoClaw 아키텍처의 강점(에어갭 분리, 프록시 게이트, 파일 버스)을 유지
- 스파게티 원인이었던 역할 경계 불명확, ID/표시명 혼선, 채널별 맥락 단절을 해소
- 3인 에이전트가 자연스럽게 동작하는 운영체계 확립

## 2) 핵심 문제(현 상태)
- 에이전트 이름/ID가 수시 변경되어 코드 경로 분기 증가
- Minerva(총괄)와 Hermes(리서치)의 책임 경계가 겹침
- 텔레그램/웹/큐 채널 맥락 정책이 일관되지 않음
- 보안 정책은 강하지만 운영 규칙(권한/승인/알림) 미정의 시 위험 확대 가능

## 3) 설계 원칙
1. ID 불변, 표시명 가변
   - Canonical ID 고정: minerva, clio, hermes
   - UI/호칭은 별도 필드로 관리, 런타임 라우팅 키로 사용 금지
2. 역할 단일 책임
   - Minerva: 의사결정/우선순위/오케스트레이션
   - Clio: 지식 구조화/문서화/NotebookLM 파이프라인
   - Hermes: 외부 수집/트렌드 분류/브리핑
3. 외부 입력 Zero-Trust
   - 웹 검색 결과는 데이터로만 취급, 명령으로 해석 금지
4. 채널 분리 + 안전 공유
   - 원문 히스토리는 에이전트별 격리
   - 공유는 정제 요약(shared memory)만 허용
5. 능동 알림 규칙 고정
   - 불필요한 proactive 메시지 금지

## 4) 에이전트 역할 정의(최종)
### Minerva (총괄)
- 책임: 목표 정렬, 우선순위 확정, 액션 플랜 결정
- 입력: 사용자 요청, Clio/Hermes 보고서, shared memory
- 출력: 실행 지시(task), 최종 의사결정 답변
- 금지: 무근거 추측, 외부 검색 원문 직접 신뢰

### Clio (지식관리)
- 책임: Markdown 구조화, 링크/태그/중복 정리, NotebookLM 스테이징
- 입력: verified_inbox, Hermes 보고서, 사용자 문서화 요청
- 출력: 표준화된 노트(.md), 지식 연결 제안
- 금지: 리서치 판단 주도, 외부 웹 탐색 직접 실행

### Hermes (트렌드)
- 책임: n8n 기반 외부 수집, HOT/INSIGHT/MONITOR 분류, 출처 검증
- 입력: 검색 쿼리, 스케줄 트리거
- 출력: 브리핑/리포트, Minerva/Clio 전달용 요약
- 금지: 전략 최종결정, 문서 체계화 책임 침범

## 5) 아키텍처(유지 + 보완)
- Frontend(Next.js) -> Next API(/api/chat) -> llm-proxy(FastAPI)
- llm-proxy: agent router, policy gate, conversation store, shared memory 처리
- n8n: 수집/스케줄/분기(0건 skip, 중복 차단)
- nanoclaw-agent: 파일 감시/큐 처리/문서 출력
- shared_data: inbox/outbox/archive/logs/verified_inbox/obsidian_vault

## 6) 데이터/메모리 정책
- Layer A: per-agent history (격리)
- Layer B: shared memory (정제 요약만)
  - 필드: summary, source, confidence, tags, ttl, pii_masked
  - 저신뢰 외부 정보는 quarantine
- MEMORY 파일 분리
  - MEMORY_MINERVA.md, MEMORY_CLIO.md, MEMORY_HERMES.md

## 7) 보안 보완안
1. llm-proxy 단일 게이트 강제
2. 내부 인증(x-internal-token + HMAC + replay 보호)
3. 네트워크 최소권한(내부망/듀얼망 분리)
4. 런타임 하드닝(read_only, cap_drop, no-new-privileges, tmpfs)
5. 비밀값 관리(.env.local 로컬 전용, 커밋 차단, 내부키 로테이션)
6. Telegram 보안(token 비공개, allowlist, 명령 allowlist, rate-limit)

## 8) 자연스러운 동작 규칙
- 정책 기반 알림만 허용(무분별 proactive 금지)
  - 즉시 알림: `priority >= high` and `confidence >= 0.80`
  - 일반 알림: Digest 큐로 적재 후 사용자 여정 테마 슬롯에서 발송
  - 동일 `topic_key`는 cooldown(기본 8h) 동안 중복 억제
- Morning Briefing(업무 시작 전)
  - 오늘 캘린더 일정 + 전일 야간 Hermes 트렌드 요약
  - 업무 시작 인사이트 중심
- Evening Wrap-up(일과 종료 시점)
  - 하루 누적 일반 변화 요약
  - Clio의 지식 연결 제안(기존 노트 연관)
- 텔레그램 UX 규칙
  - Markdown 헤더(`##`) 사용 금지
  - 1메시지 1핵심, 4~7줄 내 요약
  - 이모지는 카테고리당 1개 이하
  - 출처/근거는 최대 3개로 압축
- 실패 응답 표준: 원인 1줄 + 대안 1~2줄 + 다음 액션 1줄

## 9) 비용 전략
- 기본: Gemini 저비용/무료 티어 우선
- 승격: Minerva만 고성능 모델 조건부 사용
- n8n: 0건 skip + 중복 브리핑 차단
- Budget Cap: 일/월 한도 초과 시 자동 다운그레이드

## 10) 개발 로드맵
### Phase 1 (구조 안정화)
- Canonical ID 고정(minerva/clio/hermes)
- 역할 경계 코드 반영
- alias 비허용(운영 입력은 canonical only)

### Phase 2 (보안 강화)
- proxy gate 통합
- HMAC/replay 방어
- 텔레그램 allowlist/명령 분리

### Phase 3 (자연성 강화)
- proactive 규칙 적용
- 실패 표준 응답
- 채널 간 shared memory 제한 공유

### Phase 4 (운영 자동화)
- KPI/헬스 위젯
- 장애 등급/런북
- 릴리스 게이트 자동화

### Phase 5 (Minerva Telegram Orchestration)
- Minerva 발신 단일화(Clio/Hermes 직접 발신 금지)
- Inline Keyboard 액션 3종 고정
  - `[Clio에 저장]` -> Clio inbox task 생성
  - `[Hermes, 더 파고들어 줘]` -> Hermes deep-dive task 생성
  - `[Minerva, 이것 인사이트 분석해줘]` -> Minerva insight task 생성
- callback_data는 `action:event_id` 형식으로 제한

### Phase 6 (Hermes -> Clio 지식 연결)
- Hermes가 고임팩트 논문/아티클 발견 시 Clio 저장 이벤트 발행
- Clio는 Obsidian 템플릿 + NotebookLM staging 동시 출력
- DeepL 기반 번역(비한국어 소스) + 원문 링크 보존

### Phase 7 (공통 데이터 계약)
- shared memory 이벤트 스키마 고정
  - `event_id, topic_key, priority, confidence, tags, source_refs, ttl, dedupe_key`
- 저신뢰 외부 이벤트는 quarantine 라벨 처리
- Minerva 인사이트 합성 템플릿 고정
  - 사실 3개 + 연결고리 1개 + 제안 1개
  - 단정 표현은 confidence 임계치 이상에서만 허용

## 11) 완료 기준(DoD)
- ID/역할 혼선 코드상 0건
- 에이전트 책임 침범 테스트 통과
- llm-proxy 단일 게이트 통과
- 09:00 Hermes 브리핑 정책대로 동작(0건 skip)
- 텔레그램/웹 응답 정책 일관성 확보
- 보안 체크리스트 P0/P1 통과

## 12) 즉시 실행 순서(2026-03-03)
1. 이벤트/알림 정책 엔진 구현
   - `/api/orchestration/events`로 Hermes/Clio 이벤트 수집
   - 즉시/다이제스트/쿨다운 판정
2. Telegram callback 액션 구현
   - `/api/telegram/webhook`에서 Inline Keyboard 콜백 처리
3. shared_memory 저장소 고정
   - `shared_data/shared_memory/agent_events.json`
   - `shared_data/shared_memory/topic_cooldowns.json`
   - `shared_data/shared_memory/digest_queue.json`
4. Hermes/n8n 워크플로 연동
   - Hermes 브리핑 결과를 `/api/orchestration/events`로 publish
5. Minerva 발신 포맷 고정
   - 헤더/과도한 마크다운 제거
   - 출처 + 인사이트를 포함한 자연어 브리핑 템플릿 적용
6. Google Calendar read-only OAuth 베이스
   - `/api/integrations/google-calendar/oauth/start|callback|today`
   - scope는 `calendar.readonly`로 고정
7. Minerva Morning Calendar 합성
   - Morning theme(`05~12`) 즉시 브리핑에 당일 캘린더 요약을 주입
8. Hermes 고임팩트 auto Clio 저장
   - `priority=high|critical` + impact/tag 조건 시 `createInboxTask(clio)` 자동 생성
9. n8n 스케줄 자동화 고정
   - Hermes workflow에 오전/오후 KST 스케줄 트리거 내장
