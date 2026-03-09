from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from clio_core import ClioPipelineResult, _extract_source_lines, _truncate_text, _yaml_scalar


def _render_frontmatter(frontmatter: dict[str, Any]) -> list[str]:
    lines = ["---"]
    ordered_keys = [
        "clio_format_version",
        "title",
        "type",
        "tags",
        "status",
        "created",
        "updated",
        "source_type",
        "source_url",
        "project_links",
        "moc_candidates",
        "draft_state",
        "template_name",
        "classification_confidence",
        "note_action",
        "update_target",
        "update_target_path",
        "merge_candidates",
        "merge_candidate_paths",
        "suggestion_score",
        "suggestion_reasons",
    ]
    for key in ordered_keys:
        if key not in frontmatter:
            continue
        value = frontmatter[key]
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
                continue
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_yaml_scalar(item)}")
            continue
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return lines


def _render_article_sections(title: str, summary: str, source_lines: list[str], related_notes: list[str], projects: list[str]) -> list[str]:
    point_three = "PM 관점에서 실제 제품/지표에 어떤 변화가 생기는지 검토 필요"
    if projects:
        point_three = f"{projects[0]}와 연결되는 시사점 검토 필요"
    return [
        "## 한 줄 요약",
        summary,
        "",
        "## 3가지 핵심 포인트",
        f"1. **핵심 주장** — {summary}",
        "2. **근거 확인** — 원문/출처 세부 검토가 추가로 필요합니다.",
        f"3. **적용 포인트** — {point_three}",
        "",
        "## 인상 깊은 구절 / 데이터",
        f"> {_truncate_text(source_lines[0] if source_lines else summary, 180)}",
        "",
        "## 나의 생각",
        "PM 관점의 시사점은 사용자 검토 후 확정합니다.",
        "",
        "## 액션 아이템",
        "- [ ] 핵심 주장과 실제 적용 가능성을 검토한다.",
        "- [ ] 관련 프로젝트에 반영할 항목을 정한다.",
        "",
        "## 연결 노트",
        *([f"- 관련 노트: {item}" for item in related_notes[:3]] if related_notes else ["- 관련 노트: 사용자 검토 필요"]),
    ]


def _render_paper_sections(summary: str, source_lines: list[str], related_notes: list[str], projects: list[str]) -> list[str]:
    apply_line = f"{projects[0]}에 적용 가능성 검토" if projects else "현재 프로젝트에 적용 가능성 검토"
    return [
        "## 연구 질문",
        summary,
        "",
        "## 방법론",
        "원문 상세 검토 후 보강 필요",
        "",
        "## 핵심 결과",
        "| 지표 | 수치/결과 |",
        "|------|-----------|",
        f"| 요약 | {summary} |",
        "",
        "## 한계점",
        "- 원문 전체 검토 전까지는 해석 보류",
        "",
        "## 내 업무에의 적용",
        "> [!apply] PM/프로덕트 적용 포인트",
        f"> {apply_line}",
        "",
        "## 연결 노트",
        *([f"- 관련 노트: {item}" for item in related_notes[:3]] if related_notes else ["- 관련 노트: 사용자 검토 필요"]),
        *([f"- 원문 힌트: {source_lines[0]}"] if source_lines else []),
    ]


def _render_study_sections(summary: str, related_notes: list[str]) -> list[str]:
    return [
        "## 핵심 개념",
        summary,
        "",
        "## 상세 내용",
        "",
        "### 정의",
        f"- {summary}",
        "",
        "### 작동 원리 / 구조",
        "- 사용자 검토 후 보강",
        "",
        "### 주의할 점",
        "- 시험/실전 함정은 추가 정리 필요",
        "",
        "## 기출 & 실전 포인트",
        "",
        "> [!exam] 시험 출제 포인트",
        "> 자주 헷갈리는 정의와 예외를 보강하세요.",
        "",
        "## 연결 노트",
        *([f"- 관련 노트: {item}" for item in related_notes[:3]] if related_notes else ["- 관련 노트: 사용자 검토 필요"]),
        "",
        "## 나만의 정리 (한 줄 요약)",
        "",
        "> 사용자 검토 후 일상 언어 요약 추가",
    ]


def _render_skill_sections(summary: str, related_notes: list[str], projects: list[str]) -> list[str]:
    apply_target = ", ".join(projects[:2]) if projects else "현재 프로젝트"
    return [
        "## 한 줄 정의",
        summary,
        "",
        "## 언제 사용하는가",
        "구체적 시나리오는 사용자 검토 후 보강",
        "",
        "## 핵심 구성 요소",
        "| 요소 | 설명 |",
        "|------|------|",
        f"| 핵심 | {summary} |",
        "",
        "## 적용 사례",
        "",
        "### 교과서적 사례",
        "대표 사례 보강 필요",
        "",
        "### 내 프로젝트 적용",
        f"{apply_target}에 어떻게 적용할지 검토",
        "",
        "## 실전 팁",
        "",
        "> [!tip] 실무에서 주의할 점",
        "> 추상 설명으로 끝내지 말고 실제 프로젝트 행동으로 연결하세요.",
        "",
        "## 관련 스킬",
        *([f"- 관련 노트: {item}" for item in related_notes[:3]] if related_notes else ["- 관련 노트: 사용자 검토 필요"]),
    ]


def _render_knowledge_sections(summary: str, related_notes: list[str]) -> list[str]:
    return [
        "## 핵심 주장",
        summary,
        "",
        "## 왜 이렇게 생각하는가",
        "근거와 경험은 사용자 검토 후 보강",
        "",
        "## 구체적 사례",
        "- 사례 1: 사용자 검토 필요",
        "- 사례 2: 사용자 검토 필요",
        "",
        "## 반론 / 예외",
        "이 주장이 틀릴 수 있는 조건을 보강하세요.",
        "",
        "## 연결 노트",
        *([f"- 관련 노트: {item}" for item in related_notes[:3]] if related_notes else ["- 관련 노트: 사용자 검토 필요"]),
    ]


def _render_writing_sections(summary: str, related_notes: list[str], projects: list[str]) -> list[str]:
    audience_hint = projects[0] if projects else "타겟 독자"
    return [
        "## 핵심 메시지 (1문장)",
        summary,
        "",
        "## 아웃라인",
        "1. **도입** — 문제를 왜 지금 읽어야 하는지 제시",
        "2. **본론 1** — 핵심 주장",
        "3. **본론 2** — 사례 또는 근거",
        "4. **본론 3** — 적용 또는 관점 차이",
        "5. **결론** — 다음 행동/CTA",
        "",
        "## 초안",
        f"{audience_hint} 기준으로 초안을 확장하세요.",
        "",
        "## 퇴고 체크리스트",
        "- [ ] 첫 문장이 충분히 강한가?",
        "- [ ] 불필요한 설명을 줄였는가?",
        "- [ ] 사례나 데이터가 포함됐는가?",
        "",
        "## 참고 자료",
        *([f"- {item}" for item in related_notes[:3]] if related_notes else ["- 사용자 검토 필요"]),
    ]


def _render_note_sections(
    note_type: str,
    title: str,
    summary: str,
    source_lines: list[str],
    related_notes: list[str],
    project_names: list[str],
) -> list[str]:
    if note_type == "article":
        return _render_article_sections(title, summary, source_lines, related_notes, project_names)
    if note_type == "paper":
        return _render_paper_sections(summary, source_lines, related_notes, project_names)
    if note_type == "study":
        return _render_study_sections(summary, related_notes)
    if note_type == "skill":
        return _render_skill_sections(summary, related_notes, project_names)
    if note_type == "writing":
        return _render_writing_sections(summary, related_notes, project_names)
    return _render_knowledge_sections(summary, related_notes)


def build_markdown(
    agent_id: str,
    source: str,
    message: str,
    fallback_used: bool,
    clio: ClioPipelineResult | None = None,
) -> str:
    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    lines = []
    if clio is not None:
        frontmatter = dict(clio.frontmatter)
        frontmatter["source_url"] = clio.source_urls[0] if clio.source_urls else ""
        lines.extend(_render_frontmatter(frontmatter))
        lines.append("")
        lines.extend(
            _render_note_sections(
                clio.note_type,
                clio.title,
                clio.notebooklm_summary,
                _extract_source_lines(message),
                clio.related_notes,
                [item.strip("[]") for item in clio.project_links],
            )
        )
    else:
        lines.extend(
            [
                "---",
                f"agent_id: {_yaml_scalar(agent_id)}",
                f"timestamp: {_yaml_scalar(timestamp)}",
                f"source: {_yaml_scalar(source)}",
                f"fallback_used: {str(fallback_used).lower()}",
                "---",
                "",
                f"# NanoClaw Inbox Capture ({agent_id})",
                "",
                "## Message",
                message,
            ]
        )
    return "\n".join(lines)
