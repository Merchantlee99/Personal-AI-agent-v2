# Clio v2 Role And Artifact Spec

이 문서는 `Clio`를 Template-driven Obsidian knowledge editor로 정의하고, 현재 구현 기준을 정리합니다.

## 1) 한 줄 정의

`Clio`는 `Hermes`/사용자 입력을 Obsidian 표준 노트 초안으로 변환하고, 템플릿·태그·링크·프로젝트 연결·MOC 후보를 관리하는 지식 편집 에이전트입니다.

중요
- `Clio`는 저자가 아닙니다.
- 최종 의미 부여와 claim 확정은 사용자가 합니다.

## 2) 현재 운영에서 해결한 문제
기존 문제
- H1 기반 캡처 문서
- 해시태그형 태그
- 문서 유형 템플릿 없음
- project mention만으로 잘못된 폴더 라우팅
- timestamp prefix 파일명
- user-facing vault 안에 runtime/test/support 파일 혼입

현재 방향
- Clio는 user-facing note만 `obsidian_vault`에 저장
- Minerva/Hermes runtime note는 `runtime_agent_notes`로 분리
- verified payload, queue, support file은 vault 밖에 둠

## 3) 역할

### Do
- 입력을 `study/article/paper/knowledge/writing/skill` 중 하나로 분류
- 적절한 Obsidian 템플릿 선택
- YAML frontmatter 생성
- 태그 taxonomy 적용
- folder 라우팅
- `[[wikilink]]` 기반 관련 노트 연결
- project link / MOC 후보 생성
- draft 저장
- knowledge claim review, note suggestion, update/merge 제안 생성

### Do Not
- 사용자 claim 자동 확정
- evergreen 자동 승격
- raw 외부 텍스트를 그대로 장문 저장
- 프로젝트 적용을 근거 없이 단정
- H1 기반 자유형 캡처 문서 생성
- runtime metadata를 user-facing note 본문에 노출

## 4) 문서 유형과 기본 위치

| Type | 정의 | 기본 저장 위치 |
|---|---|---|
| `study` | 자격증/개념 학습 노트 | `01-Knowledge/` |
| `article` | 뉴스/블로그/뉴스레터 정리 | `02-References/` |
| `paper` | 학술 논문/리서치 정리 | `02-References/` |
| `knowledge` | 사용자의 주장/경험/원칙 | `01-Knowledge/` |
| `writing` | 발행용 초안 | `04-Writing/` |
| `skill` | PM 스킬/프레임워크 학습 | `01-Knowledge/` |

원칙
- project mention만으로 `03-Projects/`로 보내지 않는다.
- 명시적 `[project_note: true]`일 때만 project hub 직하위에 둔다.
- 분류 실패 시 `00-Inbox/ draft` fallback이 가능하되, user-facing vault를 더럽히지 않는 방향을 우선한다.

## 5) user-facing vault 규칙

live vault:
- [shared_data/obsidian_vault](/Users/isanginn/Workspace/Agent_Workspace/shared_data/obsidian_vault)

여기에는 사람이 읽을 노트만 둔다.
- `01-Knowledge`
- `02-References`
- `03-Projects`
- `04-Writing`
- `05-Daily`
- `06-MOCs`
- `Home.md`

vault 밖으로 분리할 것
- runtime markdown
- verification artifact
- queue/raw state/log
- template/support file

runtime/support 경로
- [shared_data/runtime_agent_notes](/Users/isanginn/Workspace/Agent_Workspace/shared_data/runtime_agent_notes)
- [shared_data/runtime/obsidian_support](/Users/isanginn/Workspace/Agent_Workspace/shared_data/runtime/obsidian_support)
- [shared_data/archive](/Users/isanginn/Workspace/Agent_Workspace/shared_data/archive)

## 6) Obsidian 저장 규칙

### 공통 frontmatter
```yaml
---
title: "문서 제목"
type: study | article | paper | knowledge | writing | skill
tags:
  - type/article
  - domain/pm
  - status/seed
status: seed | growing | evergreen
created: YYYY-MM-DD
updated: YYYY-MM-DD
source_type: hermes | user | minerva
source_url:
project_links: []
moc_candidates: []
draft_state: draft | confirmed | evergreen_candidate
---
```

원칙
- H1 사용 금지
- 본문은 `##`부터 시작
- 태그는 계층형 사용
- 링크는 `[[wikilink]]` 우선
- 파일명은 human-readable title 우선, timestamp prefix 금지

## 7) 템플릿 정책
Clio는 아래 6개 템플릿을 사용합니다.
1. `tpl-study.md`
2. `tpl-article.md`
3. `tpl-paper.md`
4. `tpl-knowledge.md`
5. `tpl-writing.md`
6. `tpl-skill.md`

템플릿별 필수 섹션은 누락 없이 채워야 합니다.

특히 `knowledge`
- 제목은 주장형 제목을 우선한다.
- `핵심 주장 -> 근거 -> 사례 -> 반론` 구조를 유지한다.

## 8) 태그 taxonomy
Clio는 아래 축만 씁니다.
- `type/*`
- `domain/*`
- `status/*`
- `source/*`
- `project/*`

금지
- `#clio`
- 비정규 free-form hashtag 남발
- runtime/system 목적 태그를 user-facing note에 남기는 것

## 9) 상태와 승인
### draft_state
- `draft`
- `confirmed`
- `review`
- `evergreen_candidate`

### status
- `seed`
- `growing`
- `evergreen`

원칙
- 자동 저장은 `draft`까지만
- `knowledge`는 사용자 승인 전 `confirmed` 이상으로 올리지 않음
- `evergreen` 자동 승격 금지

## 10) suggestion / review
Clio는 아래 artifact를 관리합니다.
- knowledge claim review
- note update suggestion
- note merge suggestion

현재 구현
- `/clio_reviews`
- `/clio_suggestions`
- 점수/근거/변경 요약 포함
- dismiss cooldown 적용
- Telegram 2단계 승인 큐 연결

## 11) 아티팩트 계약
핵심 출력은 `note_draft`입니다.

```json
{
  "artifactType": "note_draft",
  "producedBy": "clio",
  "topicKey": "string",
  "type": "article",
  "title": "string",
  "folder": "02-References/",
  "markdown": "string",
  "frontmatter": {},
  "tags": [],
  "projectLinks": [],
  "mocCandidates": [],
  "relatedNotes": [],
  "draftState": "draft",
  "claimReviewRequired": false,
  "claimReviewId": null,
  "verified": false
}
```

현재 verified payload에는 아래가 포함됩니다.
- `type`
- `folder`
- `frontmatter`
- `project_links`
- `moc_candidates`
- `related_notes`
- `draft_state`
- `template_name`
- `classification_confidence`
- `claim_review_required`
- `claim_review_id`
- `note_action`
- `update_target`
- `merge_candidates`
- `suggestion_score`
- `suggestion_reasons`

## 12) 구현 파일
- `agent/main.py`
- `proxy/app/pipeline_contract.py`
- `shared_data/verified_inbox/*`
- `shared_data/shared_memory/clio_knowledge_memory.json`
- `shared_data/shared_memory/clio_claim_review_queue.json`
- `config/tag_taxonomy.json`
- `config/project_registry.json`
- `config/moc_registry.json`

## 13) 현재 남은 보완
1. real input 기준 `article/paper` 품질 검증 확대
2. note 재사용률 계측
3. evergreen 승격 정책은 여전히 수동/보수적으로 유지
