# NanoClaw v2 Use Cases

이 문서는 "사용자가 어떤 입력을 주고, 시스템이 어떤 산출물을 내는지"를 시나리오별로 설명합니다.

## 1) 시나리오 요약

| 시나리오 | 시작점 | 핵심 처리 | 산출물 |
|---|---|---|---|
| 아침 브리핑 수신 | n8n schedule(P0/P1/P2) | Hermes 수집 -> Minerva 브리핑 | Telegram 브리핑 + event 로그 |
| Clio 저장 | Telegram 인라인 버튼 | callback -> inbox task -> agent 처리 | Obsidian md + verified payload |
| Hermes 추가 수집 | Telegram 인라인 버튼 | callback -> hermes task | 추가 근거 + outbox |
| Minerva 인사이트 | Telegram 인라인 버튼 | callback -> minerva task | 우선순위 액션 인사이트 |
| Telegram 일반 대화 | Telegram 일반 텍스트 | `/api/chat(agent=minerva)` 호출 | 대화 응답 + chat history |

## 2) 브리핑 수신 시나리오

입력
- `Hermes Daily Briefing Workflow` 스케줄 트리거

처리
1. P0/P1/P2 소스 수집
2. injection/unsafe URL 필터링
3. dedup guard
4. `/api/orchestration/events`로 이벤트 발행
5. 정책 엔진(즉시/다이제스트/쿨다운)
6. Telegram 브리핑 발송

결과물
- Telegram 메시지(주제/핵심 요약/출처/Minerva 인사이트)
- `shared_data/shared_memory/agent_events.json`
- `shared_data/shared_memory/memory.md`

## 3) Clio 저장 시나리오

입력
- Telegram 인라인: `Clio, 옵시디언에 저장해`

처리
1. webhook secret + allowlist + action allowlist 검증
2. `shared_data/inbox/*.json` task 생성
3. agent가 파일 감시 후 처리
4. Clio 메타 태깅/링크/요약 생성

결과물
- `shared_data/obsidian_vault/YYYY-MM-DD/*.md`
- `shared_data/verified_inbox/*.json`
- `shared_data/outbox/*.json`

## 4) Hermes 추가 수집 시나리오

입력
- Telegram 인라인: `Hermes, 더 찾아`

처리
1. hermes deep-dive task 생성
2. agent가 deep-dive 결과를 outbox/vault로 반영
3. `HERMES_DEEP_DIVE_AUTO_MINERVA=true`면 Minerva follow-up task 자동 생성

결과물
- Hermes deep-dive 산출물
- (옵션) Minerva 후속 인사이트 태스크

## 5) Minerva 인사이트 시나리오

입력
- Telegram 인라인: `Minerva, 인사이트 분석해`

처리
1. minerva task 생성
2. Minerva가 2차 사고(인과/파급/우선순위) 중심으로 분석

결과물
- Minerva 분석 메시지
- 관련 이벤트/메모리 누적

## 6) Telegram 일반 대화 시나리오

입력
- Telegram 텍스트 (`/help`, `/reset`, 일반 질의)

처리
1. webhook secret 검증
2. 사용자/채팅 allowlist 검증
3. rate-limit 적용
4. chat history 로드
5. `/api/chat(agent=minerva)` 호출
6. 응답 전송 + history/compact memory 저장

결과물
- Telegram 응답
- `shared_data/shared_memory/telegram_chat_history.json`
- `shared_data/shared_memory/compact_memory.json`

## 7) End-to-End 사용자 여정

```mermaid
flowchart TD
  A["n8n 스케줄"] --> B["Hermes 브리핑 이벤트"]
  B --> C["Minerva Telegram 브리핑"]
  C --> D1["Clio, 옵시디언에 저장해"]
  C --> D2["Hermes, 더 찾아"]
  C --> D3["Minerva, 인사이트 분석해"]
  C --> D4["일반 텍스트 대화"]

  D1 --> E1["inbox -> agent -> vault/verified"]
  D2 --> E2["inbox -> agent deep-dive"]
  D2 --> E3["(옵션) Minerva follow-up"]
  D3 --> E4["Minerva action insight"]
  D4 --> E5["/api/chat -> llm-proxy"]
```

## 8) 현재 제약(운영자가 알아야 할 점)
- 인라인 액션은 현재 "승인 큐" 없이 즉시 task 생성됩니다.
- Telegram 외 채널(Slack/Email) 추상화는 아직 없습니다.
- 이벤트 스키마 버전(`schema_version`) 강제는 아직 없습니다.

