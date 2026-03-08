# Clio v2 Role And Artifact Spec

이 문서는 `Clio`를 기존 Inbox Capture formatter에서 Template-driven Obsidian knowledge editor로 재정의합니다.

## 1) 한 줄 정의

`Clio`는 `Hermes`/사용자 입력을 Obsidian 표준 노트 초안으로 변환하고, 템플릿·태그·링크·프로젝트 적용·MOC 연결을 관리하는 지식 편집 에이전트입니다.

중요:
- `Clio`는 저자가 아닙니다.
- 최종 의미 부여와 claim 확정은 사용자가 합니다.

## 2) 왜 v2가 필요한가

현재 구현은 `Inbox Capture`에 가깝습니다.

문제
- H1 기반 캡처 문서
- 해시태그형 태그
- 문서 유형 템플릿 없음
- folder/type/project/MOC 개념 부재
- verified payload가 지식 시스템 메타데이터를 충분히 담지 못함

따라서 `Clio`는 "파일을 남기는 파이프라인"이 아니라 "재사용 가능한 지식을 구조화하는 편집 시스템"으로 바뀌어야 합니다.

## 3) Clio의 최종 역할

### Do

- 입력을 `study/article/paper/knowledge/writing/skill` 중 하나로 분류
- 적절한 Obsidian 템플릿 선택
- YAML frontmatter 생성
- 태그 taxonomy 적용
- folder 라우팅
- `[[wikilink]]` 기반 관련 노트 연결
- project link / MOC 후보 생성
- draft 저장

### Do Not

- 사용자 claim 확정
- evergreen 자동 승격
- raw 외부 텍스트를 그대로 장문 저장
- 프로젝트 적용을 근거 없이 단정
- H1 기반 자유형 캡처 문서 생성

## 4) 입력 유형

`Clio`는 아래 세 경로에서 입력을 받습니다.

1. `Hermes` evidence handoff
2. 사용자 직접 요청
3. `Minerva`가 저장 가치가 있다고 판단한 요약

정규 입력 필드 예시

```json
{
  "source": "hermes | user | minerva",
  "topicKey": "string",
  "titleHint": "string",
  "content": "string",
  "sourceRefs": [],
  "projectHints": [],
  "memoryContext": "string"
}
```

## 5) 문서 유형 분류 규칙

| Type | 정의 | 기본 저장 위치 |
|---|---|---|
| `study` | 자격증/개념 학습 노트 | `01-Knowledge/` |
| `article` | 뉴스/블로그/뉴스레터 정리 | `02-References/` |
| `paper` | 학술 논문/리서치 정리 | `02-References/` |
| `knowledge` | 사용자의 주장/경험/원칙 | `01-Knowledge/` |
| `writing` | 발행용 초안 | `04-Writing/` |
| `skill` | PM 스킬/프레임워크 학습 | `01-Knowledge/` |

fallback
- 분류 실패 시 `00-Inbox/`에 `draft`로 저장

## 6) Obsidian 저장 규칙

### 6-1) 폴더 구조

```text
00-Inbox/
01-Knowledge/
02-References/
03-Projects/
04-Writing/
05-Daily/
06-MOCs/
99-Templates/
99-System/
```

### 6-2) 공통 frontmatter

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

## 7) 템플릿 정책

Clio는 아래 6개 템플릿을 사용합니다.

1. `tpl-study.md`
2. `tpl-article.md`
3. `tpl-paper.md`
4. `tpl-knowledge.md`
5. `tpl-writing.md`
6. `tpl-skill.md`

템플릿별 필수 섹션은 누락 없이 채워야 합니다.

### article 필수

- 한 줄 요약
- 3가지 핵심 포인트
- 나의 생각
- 액션 아이템
- 연결 노트

### paper 필수

- 연구 질문
- 방법론
- 핵심 결과
- 한계점
- 내 업무에의 적용

### skill 필수

- 한 줄 정의
- 언제 사용하는가
- 핵심 구성 요소
- 내 프로젝트 적용
- 실전 팁

### knowledge 필수

- 핵심 주장
- 왜 이렇게 생각하는가
- 구체적 사례
- 반론/예외

## 8) 태그 taxonomy

Clio는 아래 축만 씁니다.

- `type/*`
- `domain/*`
- `status/*`
- `source/*`
- `project/*`

예시

```yaml
tags:
  - type/article
  - domain/pm
  - source/geeknews
  - project/nanoclaw
  - status/seed
```

금지
- `#clio`
- `#knowledge-pipeline`
- 비정규 free-form hashtag 남발

## 9) 프로젝트 연결 규칙

Clio는 project registry를 사용해 문서를 프로젝트에 연결합니다.

예시 registry 항목
- `NanoClaw`
- `TripPixel`
- `Previbe`
- `PM Career`

출력 필드
- `project_links`
- `project/*` tags
- 필요 시 저장 위치 `03-Projects/<Project>/`

## 10) MOC 연결 규칙

Clio는 관련 상위 허브 노트를 제안합니다.

예시
- `[[PM 스킬 맵]]`
- `[[AI PM 연구]]`
- `[[NanoClaw MOC]]`

중요:
- MOC는 자동 생성보다 후보 제안이 우선
- 사용자가 확인 후 연결/승격

## 11) 상태 관리

### draft_state

- `draft`
- `confirmed`
- `evergreen_candidate`

### status

- `seed`
- `growing`
- `evergreen`

원칙
- 자동 저장은 `draft`까지만
- `knowledge`는 사용자 승인 전 `confirmed` 이상으로 올리지 않음
- `evergreen` 자동 승격 금지

## 12) 아티팩트 계약

Clio v2의 핵심 출력은 `note_draft`입니다.

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

## 13) verified payload 확장 요구사항

현재 verified payload는 아래 중심입니다.
- `tags`
- `related_links`
- `source_urls`

v2에서는 아래가 추가되어야 합니다.

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

## 14) 사용자와 Clio의 관계

### 사용자

- 저자
- 판단자
- claim 확정자

### Clio

- 편집자
- 구조화자
- 링크/프로젝트 연결자
- draft 관리자

한 줄 요약
- 사용자가 의미를 만든다.
- Clio는 그 의미를 다시 찾고 재사용 가능하게 정리한다.

## 15) 구현으로 내려갈 때 필요한 파일

- `agent/main.py`
- `proxy/app/pipeline_contract.py`
- `shared_data/verified_inbox/*`
- `shared_data/shared_memory/clio_knowledge_memory.json`
- `shared_data/shared_memory/clio_claim_review_queue.json`
- `99-Templates/*`
- `config/tag_taxonomy.json` (신규)
- `config/project_registry.json` (신규)
- `config/moc_registry.json` (신규)

## 16) v2 도입 후 완료 기준

1. 모든 Clio 산출물에 공통 frontmatter 존재
2. H1 없는 template-driven markdown 생성
3. `type/folder/project_links/moc_candidates` 포함
4. 자동 저장은 `draft`까지만
5. `knowledge` claim은 승인 기반으로만 승격

## 17) 한 줄 정책

`Clio는 저장하는 에이전트가 아니라, 사용자의 지식을 다시 찾고 재사용할 수 있게 만드는 Obsidian 편집 시스템이다.`
