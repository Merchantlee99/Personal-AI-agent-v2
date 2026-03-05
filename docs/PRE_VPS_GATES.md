# Pre-VPS Gates (필수 선행 과제)

이 문서는 VPS 이전 전에 반드시 통과해야 하는 선행 과제를 정의합니다.

원칙:
- VPS 전환은 **기능 미완료 상태에서 진행하지 않는다**.
- Canonical Agent ID는 계속 `minerva`, `clio`, `hermes`만 사용한다.
- `Aegis`는 대화형 에이전트가 아니라 운영 감시/격리용 control-plane 역할로만 다룬다.

## Gate 1. Frontend 운영 가시성
- 목적: "알림 미도착/장애 원인 불명"을 UI에서 즉시 확인 가능하게 만들기.
- 완료 조건:
  - `/api/runtime-metrics`를 이용한 운영 상태 카드(최근 전송 성공률, pending approvals, DeepL 성공률) 표시
  - Telegram webhook 상태 점검 결과를 UI 또는 운영 페이지에서 확인 가능
  - 브리핑 미도착(예: 09:05) 감지 결과를 로그/알림으로 확인 가능
- 검증:
  - `npm run verify:orchestration`
  - `npm run verify:telegram:chat`
  - `npm run verify:hermes:schedule`

## Gate 2. Clio Obsidian 포맷 계약 고정
- 목적: 저장 포맷 드리프트 방지, NotebookLM/검색 파이프라인 재사용성 확보.
- 완료 조건:
  - Clio 산출물에 포맷 버전(`clio_obsidian_v1`) 명시
  - verified payload와 vault markdown 모두 계약 필드 유지
  - 포맷 검증 스크립트가 PASS
- 검증:
  - `npm run verify:clio-e2e`
  - `npm run verify:clio:format`

## Gate 3. NotebookLM 운영 연결
- 목적: "코드는 있으나 실사용 불가" 상태 제거.
- 완료 조건:
  - `NOTEBOOKLM_SYNC_ENABLED=true` 실운영 검증 1회 성공
  - endpoint, timeout, 실패 처리(reason 코드) 확인
  - 장애 시 Clio 파이프라인 본체는 계속 동작(NotebookLM만 degraded)
- 검증:
  - `npm run verify:clio-e2e`에서 `notebooklm_dispatch.delivered=true` (실연동 모드)

## Gate 4. Aegis(기획) 확정
- 목적: VPS 이전 전 운영/보안 감시 정책을 먼저 확정.
- 완료 조건:
  - `docs/AEGIS_PLAN.md` 기준 P0 이벤트/자동 격리 정책 합의
  - 자동 동작 범위는 "격리(containment)까지", 복구/수정은 human-in-the-loop
- 검증:
  - 문서 합의 + 스크립트/알림 룰 초안 리뷰 완료

## Gate 5. 문서-코드 일치성
- 목적: 운영자 혼란 제거.
- 완료 조건:
  - `IMPLEMENTATION_COVERAGE.md` 상태값과 실제 코드가 일치
  - 과거 미구현 표기(승인큐/runtime metrics 등) 정정
- 검증:
  - 문서 리뷰 + 핵심 검증 스크립트 PASS 로그 첨부

## 진행 순서 (권장)
1. Gate 2 (Clio 포맷 계약)  
2. Gate 1 (Frontend 운영 가시성)  
3. Gate 3 (NotebookLM 실연동)  
4. Gate 4 (Aegis 정책 확정)  
5. Gate 5 (문서 정합성 마감)
