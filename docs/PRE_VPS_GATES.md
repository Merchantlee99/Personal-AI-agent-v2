# Pre-VPS Gates (필수 선행 과제)

이 문서는 VPS 이전 전에 반드시 통과해야 하는 선행 과제를 현재 구조 기준으로 정의합니다.

원칙
- VPS 전환은 기능 미완료 상태에서 진행하지 않는다.
- Canonical Agent ID는 `minerva`, `clio`, `hermes`만 사용한다.
- `Aegis`는 대화형 에이전트가 아니라 운영 감시/격리용 control-plane으로만 다룬다.
- local Obsidian vault 경계를 무너뜨리는 구조는 VPS 이전 전에 금지한다.

## Gate 1. Morning Briefing 신뢰성

목적
- "매일 아침 브리핑이 실제로 온다"를 운영 데이터로 증명한다.

완료 조건
- 7일 연속 `09:00 KST` 브리핑 성공 로그 확보
- 08:55 preflight 자동 점검 기록 확보
- 브리핑 미도착 시 첫 실패 단계가 로그에서 바로 보임

검증
- `npm run verify:morning:preflight`
- `npm run verify:hermes:schedule`
- `npm run verify:runtime:drift`
- `npm run verify:morning:report`

## Gate 2. Clio Obsidian 계약 고정

목적
- 저장 포맷 드리프트와 user-facing vault 오염 방지

완료 조건
- Clio 산출물에 포맷 버전(`clio_obsidian_v2`) 유지
- user-facing note 본문에 runtime metadata 섹션 없음
- runtime/test/support 산출물이 vault 밖에 분리됨
- review/suggestion/approval 흐름이 PASS

검증
- `npm run verify:clio:format`
- `npm run verify:clio:suggestion`
- `npm run verify:clio:merge`
- `npm run verify:clio:approval`

## Gate 3. 운영 경계 확정

목적
- 무엇을 VPS로 옮기고 무엇을 로컬에 남길지 사전에 고정

완료 조건
- `docs/VPS_OPERATION_PLAN.md` 기준 서비스 이전 범위 합의
- local vault 유지 여부와 intermediate artifact 경계가 명시됨
- rollback 조건이 정의됨

검증
- 문서 리뷰 완료

## Gate 4. 보안 아키텍처 확정

목적
- 비용보다 먼저 공격면과 실패 반경을 고정

완료 조건
- `docs/VPS_SECURITY_ARCHITECTURE.md` 기준
  - public exposure 범위
  - SSH/VPN 접근 정책
  - secret handling
  - Docker/network boundary
  가 합의됨

검증
- 문서 리뷰 완료

## Gate 5. Aegis 정책 확정

목적
- VPS 이전 전 운영/보안 감시 정책을 먼저 확정

완료 조건
- `docs/AEGIS_PLAN.md` 기준 P0 이벤트/자동 containment 정책 합의
- 자동 동작 범위는 containment까지, 복구/수정은 human-in-the-loop

검증
- 문서 리뷰 완료

## Gate 6. NotebookLM 운영 연결 (선택)

목적
- NotebookLM을 실제로 운영에 붙일 경우, core runtime과 분리된 optional integration으로 검증

완료 조건
- NotebookLM을 VPS 이전 범위에 포함하기로 결정한 경우에만 적용
- `NOTEBOOKLM_SYNC_ENABLED=true` 실연동 검증 1회 성공
- endpoint, timeout, 실패 처리(reason 코드) 확인
- 장애 시 Clio 파이프라인 본체는 계속 동작(NotebookLM만 degraded)

검증
- `npm run verify:clio-e2e`
- NotebookLM dispatch 실제 delivered 로그 확인
- NotebookLM을 운영 범위에서 제외하면 이 Gate는 skip 가능

## Gate 7. 문서-코드 일치성

목적
- 운영자 혼란 제거

완료 조건
- `README`, `ARCHITECTURE`, `SECURITY_BASELINE`, `OPERATIONS_PLAYBOOK`, `IMPLEMENTATION_COVERAGE`가 실제 코드와 일치
- obsolete frontend 가정 제거
- live vault / runtime data 경계가 문서에 반영됨

검증
- 문서 리뷰 + 핵심 검증 스크립트 PASS 로그 첨부

## 진행 순서 (권장)
1. Gate 2 (Clio Obsidian 계약)
2. Gate 1 (Morning briefing 신뢰성)
3. Gate 3 (운영 경계 확정)
4. Gate 4 (보안 아키텍처 확정)
5. Gate 5 (Aegis 정책 확정)
6. Gate 6 (NotebookLM 실연동, 선택)
7. Gate 7 (문서 정합성 마감)
