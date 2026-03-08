from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, AnyHttpUrl, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

CANONICAL_AGENT_IDS = {"minerva", "clio", "hermes", "aegis"}
EVENT_PRIORITY_VALUES = {"critical", "high", "normal", "low"}
PRIORITY_TIER_VALUES = {"P0", "P1", "P2"}
SUMMARY_SCOPE_VALUES = {"telegram_chat", "digest", "memory_compaction", "calendar_briefing", "approval_queue", "system"}
APPROVAL_STAGE_VALUES = {"pending_stage1", "pending_stage2", "rejected", "executed"}
GUARD_SEVERITY_VALUES = {"info", "warning", "critical"}


def _compact_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return " ".join(value.split()).strip()


def _normalize_string_list(value: Any, *, lowercase: bool = False) -> Any:
    if value is None:
        return []
    if not isinstance(value, list):
        return value
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        compact = _compact_text(item)
        if not isinstance(compact, str) or not compact:
            continue
        if lowercase:
            compact = compact.lower()
        if compact in seen:
            continue
        seen.add(compact)
        normalized.append(compact)
    return normalized


class PipelineModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SourceRef(PipelineModel):
    title: str = Field(min_length=1, max_length=160)
    url: AnyHttpUrl
    snippet: str | None = Field(default=None, max_length=600)
    publisher: str | None = Field(default=None, max_length=120)
    publishedAt: datetime | None = None
    category: str | None = Field(default=None, max_length=80)
    priorityTier: Literal["P0", "P1", "P2"] | None = None
    domain: str | None = Field(default=None, max_length=120)

    @field_validator("title", "snippet", "publisher", "category", "domain", mode="before")
    @classmethod
    def compact_optional_text(cls, value: Any) -> Any:
        compact = _compact_text(value)
        return compact or None


class EventArtifact(PipelineModel):
    schemaVersion: Literal[1] = 1
    artifactType: Literal["event"] = "event"
    agentId: Literal["minerva", "clio", "hermes", "aegis"]
    topicKey: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1200)
    priority: Literal["critical", "high", "normal", "low"]
    confidence: float = Field(ge=0, le=1)
    tags: list[str] = Field(default_factory=list, max_length=24)
    sourceRefs: list[SourceRef] = Field(default_factory=list, max_length=12)
    impactScore: float | None = Field(default=None, ge=0, le=1)
    insightHint: str | None = Field(default=None, max_length=200)
    payload: dict[str, Any] | None = None
    chatId: str | None = Field(default=None, max_length=80)
    forceDispatch: bool | None = None
    forceTheme: Literal["morning_briefing", "evening_wrapup", "adhoc"] | None = None

    @field_validator("topicKey", "title", "summary", "insightHint", "chatId", mode="before")
    @classmethod
    def compact_text_fields(cls, value: Any) -> Any:
        compact = _compact_text(value)
        return compact or None

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: Any) -> Any:
        return _normalize_string_list(value, lowercase=True)

    @field_validator("sourceRefs", mode="before")
    @classmethod
    def default_source_refs(cls, value: Any) -> Any:
        if value is None:
            return []
        return value


class EvidenceItem(PipelineModel):
    title: str = Field(min_length=1, max_length=200)
    url: AnyHttpUrl
    snippet: str = Field(min_length=1, max_length=1200)
    publisher: str | None = Field(default=None, max_length=120)
    publishedAt: datetime | None = None
    category: str | None = Field(default=None, max_length=80)
    priorityTier: Literal["P0", "P1", "P2"] | None = None
    domain: str | None = Field(default=None, max_length=120)
    relevanceScore: float | None = Field(default=None, ge=0, le=1)

    @field_validator("title", "snippet", "publisher", "category", "domain", mode="before")
    @classmethod
    def compact_item_fields(cls, value: Any) -> Any:
        compact = _compact_text(value)
        return compact or None


class EvidenceSecurityStats(PipelineModel):
    promptInjectionFiltered: int = Field(default=0, ge=0)
    duplicateFiltered: int = Field(default=0, ge=0)
    blockedDomains: int = Field(default=0, ge=0)
    unsafeUrls: int = Field(default=0, ge=0)


class EvidenceSourcePlan(PipelineModel):
    categories: list[str] = Field(default_factory=list, max_length=12)
    priorityTiers: list[Literal["P0", "P1", "P2"]] = Field(default_factory=list, max_length=3)
    providers: list[str] = Field(default_factory=list, max_length=8)
    collectedAt: datetime | None = None

    @field_validator("categories", "providers", mode="before")
    @classmethod
    def normalize_plan_lists(cls, value: Any) -> Any:
        return _normalize_string_list(value, lowercase=False)


class EvidenceBundleArtifact(PipelineModel):
    schemaVersion: Literal[1] = 1
    artifactType: Literal["evidence_bundle"] = "evidence_bundle"
    producedBy: Literal["hermes"] = "hermes"
    topicKey: str = Field(min_length=1, max_length=120)
    dedupeKey: str = Field(min_length=8, max_length=64)
    items: list[EvidenceItem] = Field(min_length=1, max_length=20)
    securityStats: EvidenceSecurityStats
    sourcePlan: EvidenceSourcePlan

    @field_validator("topicKey", "dedupeKey", mode="before")
    @classmethod
    def compact_bundle_fields(cls, value: Any) -> Any:
        compact = _compact_text(value)
        return compact or None


class NoteDraftArtifact(PipelineModel):
    schemaVersion: Literal[1] = 1
    artifactType: Literal["note_draft"] = "note_draft"
    producedBy: Literal["clio"] = "clio"
    topicKey: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    noteType: Literal["study", "article", "paper", "knowledge", "writing", "skill"] = Field(
        validation_alias=AliasChoices("noteType", "type"),
        serialization_alias="type",
    )
    folder: str = Field(min_length=1, max_length=180)
    templateName: str = Field(
        min_length=1,
        max_length=120,
        validation_alias=AliasChoices("templateName", "template_name"),
        serialization_alias="template_name",
    )
    markdown: str = Field(min_length=1, max_length=20000)
    tags: list[str] = Field(default_factory=list, max_length=24)
    projectLinks: list[str] = Field(
        default_factory=list,
        max_length=12,
        validation_alias=AliasChoices("projectLinks", "project_links"),
        serialization_alias="project_links",
    )
    mocCandidates: list[str] = Field(
        default_factory=list,
        max_length=12,
        validation_alias=AliasChoices("mocCandidates", "moc_candidates"),
        serialization_alias="moc_candidates",
    )
    relatedNotes: list[str] = Field(
        default_factory=list,
        max_length=16,
        validation_alias=AliasChoices("relatedNotes", "related_notes"),
        serialization_alias="related_notes",
    )
    sourceUrls: list[AnyHttpUrl] = Field(
        default_factory=list,
        max_length=12,
        validation_alias=AliasChoices("sourceUrls", "source_urls"),
        serialization_alias="source_urls",
    )
    draftState: Literal["draft", "review", "confirmed"] = Field(
        validation_alias=AliasChoices("draftState", "draft_state"),
        serialization_alias="draft_state",
    )
    noteAction: Literal["create", "update_candidate", "merge_candidate"] = Field(
        validation_alias=AliasChoices("noteAction", "note_action"),
        serialization_alias="note_action",
    )
    updateTarget: str | None = Field(
        default=None,
        max_length=160,
        validation_alias=AliasChoices("updateTarget", "update_target"),
        serialization_alias="update_target",
    )
    updateTargetPath: str | None = Field(
        default=None,
        max_length=260,
        validation_alias=AliasChoices("updateTargetPath", "update_target_path"),
        serialization_alias="update_target_path",
    )
    mergeCandidates: list[str] = Field(
        default_factory=list,
        max_length=8,
        validation_alias=AliasChoices("mergeCandidates", "merge_candidates"),
        serialization_alias="merge_candidates",
    )
    mergeCandidatePaths: list[str] = Field(
        default_factory=list,
        max_length=8,
        validation_alias=AliasChoices("mergeCandidatePaths", "merge_candidate_paths"),
        serialization_alias="merge_candidate_paths",
    )
    classificationConfidence: float = Field(
        ge=0,
        le=1,
        validation_alias=AliasChoices("classificationConfidence", "classification_confidence"),
        serialization_alias="classification_confidence",
    )
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    verified: bool = False
    vaultPath: str | None = Field(
        default=None,
        max_length=260,
        validation_alias=AliasChoices("vaultPath", "vault_path"),
        serialization_alias="vault_path",
    )

    @field_validator("topicKey", "title", "folder", "templateName", "vaultPath", "updateTarget", "updateTargetPath", mode="before")
    @classmethod
    def compact_note_text_fields(cls, value: Any) -> Any:
        compact = _compact_text(value)
        return compact or None

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_note_tags(cls, value: Any) -> Any:
        return _normalize_string_list(value, lowercase=True)

    @field_validator("projectLinks", "mocCandidates", "relatedNotes", "mergeCandidates", "mergeCandidatePaths", mode="before")
    @classmethod
    def normalize_note_links(cls, value: Any) -> Any:
        return _normalize_string_list(value, lowercase=False)

    @field_validator("sourceUrls", mode="before")
    @classmethod
    def normalize_source_urls(cls, value: Any) -> Any:
        return _normalize_string_list(value, lowercase=False)


class SummaryWindow(PipelineModel):
    startAt: datetime
    endAt: datetime

    @model_validator(mode="after")
    def validate_window_order(self) -> "SummaryWindow":
        if self.endAt < self.startAt:
            raise ValueError("window endAt must be greater than or equal to startAt")
        return self


class SummaryBlockArtifact(PipelineModel):
    schemaVersion: Literal[1] = 1
    artifactType: Literal["summary_block"] = "summary_block"
    scope: Literal["telegram_chat", "digest", "memory_compaction", "calendar_briefing", "approval_queue", "system"]
    window: SummaryWindow
    summary: str = Field(min_length=1, max_length=2400)
    highlights: list[str] = Field(min_length=1, max_length=8)
    expiresAt: datetime

    @field_validator("summary", mode="before")
    @classmethod
    def compact_summary_text(cls, value: Any) -> Any:
        compact = _compact_text(value)
        return compact or None

    @field_validator("highlights", mode="before")
    @classmethod
    def normalize_highlights(cls, value: Any) -> Any:
        return _normalize_string_list(value, lowercase=False)


class ApprovalHistoryEntry(PipelineModel):
    at: datetime
    type: Literal["created", "stage1_approved", "rejected", "executed"]
    actorUserId: str = Field(min_length=1, max_length=80)

    @field_validator("actorUserId", mode="before")
    @classmethod
    def compact_actor_user_id(cls, value: Any) -> Any:
        compact = _compact_text(value)
        return compact or None


class ApprovalRequestArtifact(PipelineModel):
    schemaVersion: Literal[1] = 1
    artifactType: Literal["approval_request"] = "approval_request"
    approvalId: str = Field(
        min_length=8,
        max_length=32,
        validation_alias=AliasChoices("approvalId", "id"),
        serialization_alias="id",
    )
    action: str = Field(min_length=1, max_length=80)
    eventId: str = Field(min_length=1, max_length=80)
    eventTitle: str = Field(min_length=1, max_length=200)
    topicKey: str = Field(min_length=1, max_length=120)
    chatId: str = Field(min_length=1, max_length=80)
    requestedBy: str = Field(
        min_length=1,
        max_length=80,
        validation_alias=AliasChoices("requestedBy", "requestedByUserId"),
        serialization_alias="requestedByUserId",
    )
    requestedAt: datetime
    expiresAt: datetime
    requiredSteps: int = Field(ge=1, le=2)
    stage: Literal["pending_stage1", "pending_stage2", "rejected", "executed"] = Field(
        validation_alias=AliasChoices("stage", "status"),
        serialization_alias="status",
    )
    payload: dict[str, Any] | None = None
    history: list[ApprovalHistoryEntry] = Field(default_factory=list, max_length=16)

    @field_validator("action", "eventId", "eventTitle", "topicKey", "chatId", mode="before")
    @classmethod
    def compact_approval_fields(cls, value: Any) -> Any:
        compact = _compact_text(value)
        return compact or None


class GuardSignalArtifact(PipelineModel):
    schemaVersion: Literal[1] = 1
    artifactType: Literal["guard_signal"] = "guard_signal"
    producedBy: Literal["aegis"] = "aegis"
    severity: Literal["info", "warning", "critical"]
    reason: str = Field(min_length=1, max_length=240)
    service: str = Field(min_length=1, max_length=120)
    metrics: dict[str, float | int | str] = Field(default_factory=dict)
    recommendedAction: str = Field(min_length=1, max_length=240)

    @field_validator("reason", "service", "recommendedAction", mode="before")
    @classmethod
    def compact_guard_fields(cls, value: Any) -> Any:
        compact = _compact_text(value)
        return compact or None


ARTIFACT_MODEL_REGISTRY = {
    "event": EventArtifact,
    "evidence_bundle": EvidenceBundleArtifact,
    "note_draft": NoteDraftArtifact,
    "summary_block": SummaryBlockArtifact,
    "approval_request": ApprovalRequestArtifact,
    "guard_signal": GuardSignalArtifact,
}


def normalize_pipeline_artifact(
    artifact_type: str,
    payload: dict[str, Any],
    *,
    include_artifact_type: bool = True,
    by_alias: bool = False,
) -> dict[str, Any]:
    model = ARTIFACT_MODEL_REGISTRY.get(artifact_type)
    if model is None:
        raise ValueError(f"unsupported artifact_type: {artifact_type}")
    validated = model.model_validate({"artifactType": artifact_type, **payload})
    exclude = set()
    if not include_artifact_type:
        exclude.add("artifactType")
    return validated.model_dump(mode="json", by_alias=by_alias, exclude=exclude)


def normalize_event_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    return normalize_pipeline_artifact("event", payload, include_artifact_type=False)


def normalize_approval_request_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    return normalize_pipeline_artifact("approval_request", payload, include_artifact_type=False, by_alias=True)


__all__ = [
    "APPROVAL_STAGE_VALUES",
    "ApprovalRequestArtifact",
    "EvidenceBundleArtifact",
    "EventArtifact",
    "GuardSignalArtifact",
    "NoteDraftArtifact",
    "SummaryBlockArtifact",
    "ValidationError",
    "normalize_approval_request_artifact",
    "normalize_event_artifact",
    "normalize_pipeline_artifact",
]
