# NanoClaw v2 Architecture

기준 시점: 2026-03-03  
핵심: `minerva` / `clio` / `hermes` 3개 canonical ID, `llm-proxy` 단일 게이트, 내부 검증(HMAC), 최소 권한 컨테이너.

## 1. 역할 경계
- `minerva`: 오케스트레이션, 우선순위 판단, 최종 인사이트 정리
- `clio`: 문서화, 지식 정리, Obsidian/NotebookLM 준비
- `hermes`: 웹 수집, 트렌드 브리핑, 근거 확장 수집

```mermaid
flowchart TD
  CFG["config/agents.json (single source)"] --> M["minerva"]
  CFG --> C["clio"]
  CFG --> H["hermes"]
  M --> MR["orchestration / decision"]
  C --> CR["knowledge / documentation"]
  H --> HR["web signals / briefing"]
```

## 2. 전체 시스템 토폴로지

```mermaid
flowchart LR
  U["User"] --> FE["Next.js UI"]
  FE --> CHAT["POST /api/chat"]
  CHAT --> PROXY["llm-proxy /api/agent"]

  PROXY --> LLMG["Gemini API"]
  PROXY --> LLMA["Anthropic API"]
  PROXY --> SEARCH["/api/search (inert data only)"]

  N8N["n8n (schedule + webhook)"] --> ORCH["POST /api/orchestration/events"]
  ORCH --> TG["Telegram sendMessage + inline keyboard"]
  TG --> TGCB["POST /api/telegram/webhook"]
  TGCB --> INBOX["shared_data/inbox/*.json"]

  INBOX --> AGENT["nanoclaw-agent (watchdog + poll)"]
  AGENT --> OUTBOX["shared_data/outbox"]
  AGENT --> VAULT["shared_data/obsidian_vault"]
  AGENT --> VERIFIED["shared_data/verified_inbox (Clio)"]
  AGENT --> ARCHIVE["shared_data/archive"]
```

## 3. Chat 경로 (Frontend -> llm-proxy)

```mermaid
sequenceDiagram
  participant User
  participant FE as Next.js /api/chat
  participant PX as llm-proxy /api/agent
  participant LLM as LLM Provider

  User->>FE: agentId + message + history
  FE->>FE: canonical id 검증, HMAC 헤더 생성
  FE->>PX: x-internal-token + x-timestamp + x-nonce + x-signature
  PX->>PX: token -> rate-limit -> timestamp -> signature -> nonce 저장
  PX->>LLM: agent별 model routing + retry/fallback
  LLM-->>PX: reply
  PX-->>FE: agent_id, model, reply, role_boundary
  FE-->>User: UI 응답 렌더링
```

## 4. Orchestration + Telegram 인라인 액션

```mermaid
sequenceDiagram
  participant H as Hermes/n8n
  participant O as /api/orchestration/events
  participant T as Telegram
  participant W as /api/telegram/webhook
  participant I as shared_data/inbox
  participant A as nanoclaw-agent

  H->>O: 이벤트(topic/priority/confidence/sourceRefs)
  O->>O: policy 평가(send_now/queue_digest/suppressed)
  O->>T: Minerva 브리핑 + 인라인 버튼
  T->>W: callback(action:event_id)
  W->>W: webhook secret + user/chat allowlist + action allowlist 검증
  W->>I: clio_save / hermes_deep_dive / minerva_insight task 생성
  A->>I: task consume
  A->>A: Markdown/JSON 처리 + 후속 task 생성(옵션)
```

현재 인라인 버튼 UX:
- `Clio, 옵시디언에 저장해`
- `Hermes, 더 찾아`
- `Minerva, 인사이트 분석해`

`Hermes, 더 찾아`는 근거 수집 전용으로 제한되며, `HERMES_DEEP_DIVE_AUTO_MINERVA=true`일 때 처리 완료 후 Minerva 후속 인사이트 태스크를 자동 생성한다.

## 5. n8n 워크플로우 구조

```mermaid
flowchart TD
  S1["Hermes Daily Briefing Workflow"] --> F1["입력 정규화 + 중복 억제"]
  F1 --> F2["prompt-injection / unsafe-url 필터"]
  F2 --> R1["브리핑 마크다운 생성"]
  R1 --> O1["/api/orchestration/events 전송"]

  S2["Hermes Web Search Workflow (Tavily)"] --> F3["query 정규화"]
  F3 --> F4["prompt-like 제거 + 안전 URL만 유지"]
  F4 --> R2["inert_search_records_only 결과"]
```

보안 원칙:
- 검색/수집 결과는 실행하지 않고 구조화 데이터로만 하위 단계로 전달
- `localhost`, 사설 IP, 스크립트/명령 유도 텍스트는 필터링

## 6. Clio 파이프라인 구조

```mermaid
flowchart LR
  IN["inbox(clio task)"] --> P["agent/main.py process_file"]
  P --> M["obsidian_vault/*.md 생성"]
  P --> V["verified_inbox/*.json 생성"]
  V --> N["NotebookLM webhook(optional)"]
  P --> O["outbox/*.json 처리 결과 기록"]
```

Clio 처리 시 포함 정보:
- 태그 자동화(`#clio`, `#knowledge-pipeline` + 동적 태그)
- 관련 링크/출처 URL
- DeepL 번역 적용 여부
- NotebookLM sync 준비 메타

## 7. 데이터 경로

```text
shared_data/
  inbox/                 # telegram/orchestration/n8n/수동 태스크 유입
  outbox/                # 처리 결과 JSON
  archive/               # 처리 완료 원본 inbox 보관
  obsidian_vault/        # Markdown 지식 산출물
  verified_inbox/        # Clio 정제 payload
  shared_memory/         # 이벤트, digest, cooldown, oauth/token 상태
  logs/                  # llm usage metrics
```

## 8. Docker/네트워크 경계

```mermaid
flowchart LR
  subgraph INT["internal network (internal:true)"]
    AG["nanoclaw-agent"]
    PX["llm-proxy"]
    N8["n8n"]
  end
  subgraph EXT["external network (bridge)"]
    PXE["llm-proxy 외부 연동"]
    N8E["n8n 외부 연동"]
  end
  AG --> INT
  PX --> INT
  N8 --> INT
  PX --> PXE
  N8 --> N8E
```

기본 하드닝:
- `read_only: true`
- `cap_drop: [ALL]`
- `security_opt: [no-new-privileges:true]`
- `tmpfs` 사용

## 9. 이전 레포 대비 핵심 변화
- alias 입력(`ace`, `owl`, `dolphin`) 제거, canonical only 고정
- 역할별 책임 경계 명시 + Telegram 액션도 경계에 맞춰 분리
- `llm-proxy`를 유일한 모델 호출 게이트로 고정
- n8n 2개 워크플로우(스케줄 브리핑/웹검색)를 운영 스크립트와 검증 스크립트로 표준화
- Clio 검증 파이프라인(`verified_inbox`)과 NotebookLM 준비 경로 추가
