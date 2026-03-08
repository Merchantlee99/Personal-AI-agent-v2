# NanoClaw v2 Architecture

이 문서는 시스템 연결 구조와 역할 경계를 설명합니다.
운영 절차는 [OPERATIONS_PLAYBOOK](OPERATIONS_PLAYBOOK.md), 보안 통제는 [SECURITY_BASELINE](SECURITY_BASELINE.md)를 참고합니다.

## 1) 역할 분리(고정 규칙)

| Agent | 책임(Do) | 비책임(Do Not) |
|---|---|---|
| `minerva` | 오케스트레이션, 우선순위, 최종 인사이트 | 직접 대량 웹수집 파이프라인 운영 |
| `clio` | 지식 구조화, 문서화, Obsidian/NotebookLM 준비 | 실시간 트렌드 감시 의사결정 |
| `hermes` | 외부 수집, 트렌드 브리핑, 근거 확장 | 최종 전략 결론 단독 확정 |

Canonical ID는 `minerva`, `clio`, `hermes`만 허용합니다.

운영 원칙
- 사용자에게 보이는 대화 창구는 `Minerva` 하나입니다.
- `Clio`, `Hermes`, `Aegis`는 내부 worker로만 호출합니다.
- 에이전트 간 공유는 raw 자유대화가 아니라 `event/evidence/note/summary/approval` 아티팩트로 제한합니다.

## 2) 컴포넌트 구조 (Telegram-only)

```mermaid
flowchart LR
  subgraph ENTRY["External Entry"]
    TGUSER["Telegram User"]
    N8N["n8n schedule/webhook"]
    TAPI["Telegram Cloud API"]
  end

  subgraph CORE["Core Service"]
    PX["llm-proxy (FastAPI)"]
    CHAT["/api/chat"]
    ORCH["/api/orchestration/events"]
    TGCB["/api/telegram/webhook"]
    GCAL["/api/integrations/google-calendar/*"]
  end

  subgraph BRIDGE["Telegram Bridge"]
    TGP["telegram-poller"]
  end

  subgraph WORKER["Worker"]
    AG["nanoclaw-agent (watchdog)"]
  end

  subgraph STORE["Shared Data"]
    INBOX["inbox"]
    OUTBOX["outbox"]
    VAULT["obsidian_vault"]
    VERIFIED["verified_inbox"]
    MEMORY["shared_memory"]
  end

  TGUSER --> TAPI
  TAPI --> TGP
  TGP --> TGCB
  TGCB --> CHAT
  CHAT --> PX
  PX --> LLM["Gemini / Anthropic"]

  N8N --> ORCH
  ORCH --> TGAPI["Telegram sendMessage"]
  TGCB --> TGAPI

  TGCB --> INBOX
  INBOX --> AG
  AG --> OUTBOX
  AG --> VAULT
  AG --> VERIFIED

  ORCH --> MEMORY
  TGCB --> MEMORY
  GCAL --> ORCH
```

## 3) 핵심 시퀀스

### 3-1) Telegram 일반 대화

```mermaid
sequenceDiagram
  participant U as Telegram User
  participant T as Telegram Cloud
  participant TP as telegram-poller
  participant W as /api/telegram/webhook
  participant C as /api/chat
  participant PX as llm-proxy
  participant L as LLM

  U->>T: text (/help, /reset, 일반 질문)
  TP->>T: getUpdates
  T-->>TP: update
  TP->>W: forward update
  W->>C: agentId=minerva + history
  C->>PX: 모델 라우팅
  PX->>L: infer
  L-->>PX: reply
  PX-->>W: normalized reply
  W-->>U: telegram sendMessage
```

### 3-2) Hermes 스케줄 브리핑

```mermaid
sequenceDiagram
  participant S as n8n schedule(P0/P1/P2)
  participant O as /api/orchestration/events
  participant T as Telegram

  S->>O: Hermes event(topic/priority/sourceRefs)
  O->>O: policy(즉시/다이제스트/쿨다운)
  O->>T: Minerva briefing + inline buttons
```

### 3-3) Telegram 인라인 버튼 후속 처리

```mermaid
sequenceDiagram
  participant U as Telegram User
  participant T as Telegram Cloud
  participant TP as telegram-poller
  participant W as /api/telegram/webhook
  participant I as shared_data/inbox
  participant A as nanoclaw-agent

  U->>T: clio_save / hermes_deep_dive / minerva_insight
  TP->>T: getUpdates
  T-->>TP: callback update
  TP->>W: forward callback
  W->>I: inbox task 생성
  I->>A: watchdog consume
  A->>A: vault/outbox/verified 생성
  A-->>I: 원본 archive 이동
```

### 3-4) Google Calendar read-only (Telegram-only)

```mermaid
sequenceDiagram
  participant U as Telegram User
  participant W as /api/telegram/webhook
  participant G as Google OAuth + Calendar API

  U->>W: /gcal_connect
  W-->>U: OAuth authorize URL
  U->>G: consent (readonly)
  G->>W: /api/integrations/google-calendar/oauth/callback
  W-->>U: 연결 완료 알림
  U->>W: /gcal_today
  W->>G: list events (today)
  W-->>U: 일정 요약 전송
```

## 4) 설정 단일 소스

| 대상 | 파일 |
|---|---|
| 에이전트 canonical ID/역할 | `config/agents.json` |
| 에이전트 퍼소나 | `config/personas.json` |
| Minerva 대화 페르소나 기준 | `docs/MINERVA_PERSONA_SPEC.md` |
| Clio Obsidian 지식 편집 기준 | `docs/CLIO_V2_SPEC.md` |
| 역할별 메모리 분리 기준 | `docs/MEMORY_SPLIT_SPEC.md` |
| 런타임 정책/비밀값 | `.env.local` |
| Hermes 소스 분류 규칙 | `proxy/app/source_taxonomy.py` |
| 에이전트 공유 계약/오케스트레이션 기준 | `docs/AGENT_SHARED_PIPELINE.md` |

## 5) 저장소 산출물 구조

```text
shared_data/
  inbox/               # button/orchestration 기반 task 입력
  outbox/              # agent 처리 결과(JSON)
  archive/             # 처리 완료 원본
  obsidian_vault/      # Clio markdown 산출물
  verified_inbox/      # Clio 정제 payload
  shared_memory/       # events, cooldown, digest, telegram history, approval queue
```

## 6) 구현 근거 파일 맵

| 기능 | 구현 파일 |
|---|---|
| 정책 엔진(임계값/쿨다운/다이제스트) | `proxy/app/orch_policy.py` |
| 오케스트레이션 엔드포인트 | `proxy/app/main.py` (`/api/orchestration/events`) |
| Telegram 포맷/인라인 버튼/번역 정책 | `proxy/app/telegram_bridge.py` |
| Telegram webhook 처리 | `proxy/app/main.py` (`/api/telegram/webhook`) |
| Telegram polling bridge | `proxy/app/telegram_poller.py` |
| 이벤트 컨트랙트 검증 | `proxy/app/orch_contract.py` |
| 메모리/승인 큐 저장소 | `proxy/app/orch_store.py` |
| Google Calendar read-only 통합 | `proxy/app/google_calendar.py`, `proxy/app/main.py` |
| LLM 라우팅/모델 fallback | `proxy/app/main.py`, `proxy/app/llm_client.py` |
| agent 파일 파이프라인 | `agent/main.py` |
| n8n 부트스트랩 | `scripts/n8n/*.sh`, `n8n/workflows/*.json` |

## 7) 현재 아키텍처에서 의도적으로 제외된 것
- Telegram 외 채널 추상화(Slack/Email 드라이버)
- Aegis 자동 격리 런타임(현재는 계획 문서만)

이 항목들은 [IMPLEMENTATION_COVERAGE](IMPLEMENTATION_COVERAGE.md)에서 추적합니다.
