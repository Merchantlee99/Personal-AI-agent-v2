import unittest

from pydantic import ValidationError

from app.orch_contract import validate_event_contract_v1
from app.pipeline_contract import (
    ApprovalRequestArtifact,
    EvidenceBundleArtifact,
    GuardSignalArtifact,
    NoteDraftArtifact,
    SummaryBlockArtifact,
    normalize_approval_request_artifact,
)


class PipelineContractTests(unittest.TestCase):
    def test_event_contract_payload_conforms_to_event_artifact(self) -> None:
        result = validate_event_contract_v1(
            {
                "schemaVersion": 1,
                "agentId": "hermes",
                "topicKey": "kr-super-app",
                "title": "Kakao super app update",
                "summary": "Hermes found a new product and market update.",
                "priority": "high",
                "confidence": 0.88,
                "tags": ["Trend", "trend", "  super-app "],
                "sourceRefs": [
                    {
                        "title": "GeekNews summary",
                        "url": "https://news.hada.io/topic?id=1",
                        "priorityTier": "P1",
                    }
                ],
            },
            require_explicit_schema_version=True,
        )
        self.assertTrue(result["ok"])
        payload = result["payload"]
        self.assertEqual(payload["tags"], ["trend", "super-app"])
        self.assertEqual(payload["sourceRefs"][0]["priorityTier"], "P1")

    def test_evidence_bundle_requires_non_empty_items(self) -> None:
        with self.assertRaises(ValidationError):
            EvidenceBundleArtifact.model_validate(
                {
                    "topicKey": "ai-trends",
                    "dedupeKey": "abc12345",
                    "items": [],
                    "securityStats": {},
                    "sourcePlan": {},
                }
            )

    def test_note_draft_normalizes_clio_v2_fields(self) -> None:
        artifact = NoteDraftArtifact.model_validate(
            {
                "topicKey": "agent-memory",
                "title": "Agent memory note",
                "type": "knowledge",
                "folder": "01-Knowledge",
                "template_name": "tpl-knowledge.md",
                "markdown": "---\ntitle: \"Agent memory note\"\n---\n\n## 핵심 주장\nBody",
                "tags": ["AI", "ai", " memory "],
                "project_links": ["[[NanoClaw]]", "[[NanoClaw]]"],
                "moc_candidates": ["[[지식관리 MOC]]", "[[지식관리 MOC]]"],
                "related_notes": ["[[Memory]]", "[[Memory]]"],
                "source_urls": ["https://example.com", "https://example.com"],
                "draft_state": "draft",
                "note_action": "merge_candidate",
                "update_target": "",
                "update_target_path": "",
                "merge_candidates": ["[[Memory]]", "[[Memory]]"],
                "merge_candidate_paths": ["obsidian_vault/01-Knowledge/Memory.md", "obsidian_vault/01-Knowledge/Memory.md"],
                "classification_confidence": 0.81,
                "frontmatter": {"title": "Agent memory note", "type": "knowledge"},
                "verified": True,
            }
        )
        self.assertEqual(artifact.tags, ["ai", "memory"])
        self.assertEqual(artifact.noteType, "knowledge")
        self.assertEqual(artifact.projectLinks, ["[[NanoClaw]]"])
        self.assertEqual(artifact.mocCandidates, ["[[지식관리 MOC]]"])
        self.assertEqual(artifact.relatedNotes, ["[[Memory]]"])
        self.assertEqual([str(item) for item in artifact.sourceUrls], ["https://example.com/"])
        self.assertEqual(artifact.draftState, "draft")
        self.assertEqual(artifact.noteAction, "merge_candidate")
        self.assertEqual(artifact.mergeCandidates, ["[[Memory]]"])
        self.assertEqual(artifact.mergeCandidatePaths, ["obsidian_vault/01-Knowledge/Memory.md"])

    def test_summary_block_rejects_inverted_window(self) -> None:
        with self.assertRaises(ValidationError):
            SummaryBlockArtifact.model_validate(
                {
                    "scope": "digest",
                    "window": {
                        "startAt": "2026-03-07T10:00:00Z",
                        "endAt": "2026-03-07T09:00:00Z",
                    },
                    "summary": "Digest summary",
                    "highlights": ["one"],
                    "expiresAt": "2026-03-07T11:00:00Z",
                }
            )

    def test_approval_request_runtime_shape_is_normalized(self) -> None:
        payload = normalize_approval_request_artifact(
            {
                "id": "deadbeefcafe",
                "action": "clio_save",
                "eventId": "evt-1",
                "eventTitle": "Save this note",
                "topicKey": "knowledge-note",
                "chatId": "8241238117",
                "requestedByUserId": "8241238117",
                "requestedAt": "2026-03-07T10:00:00Z",
                "expiresAt": "2026-03-07T10:05:00Z",
                "requiredSteps": 2,
                "status": "pending_stage1",
                "history": [
                    {
                        "at": "2026-03-07T10:00:00Z",
                        "type": "created",
                        "actorUserId": "8241238117",
                    }
                ],
            }
        )
        self.assertEqual(payload["id"], "deadbeefcafe")
        self.assertEqual(payload["requestedByUserId"], "8241238117")
        self.assertEqual(payload["status"], "pending_stage1")

    def test_guard_signal_rejects_unknown_severity(self) -> None:
        with self.assertRaises(ValidationError):
            GuardSignalArtifact.model_validate(
                {
                    "severity": "fatal",
                    "reason": "Unexpected CPU spike",
                    "service": "llm-proxy",
                    "metrics": {"cpu": 97},
                    "recommendedAction": "Scale down",
                }
            )

    def test_approval_request_model_accepts_runtime_store_shape(self) -> None:
        artifact = ApprovalRequestArtifact.model_validate(
            {
                "id": "deadbeefcafe",
                "action": "minerva_insight",
                "eventId": "evt-2",
                "eventTitle": "Insight request",
                "topicKey": "mobility",
                "chatId": "8241238117",
                "requestedByUserId": "8241238117",
                "requestedAt": "2026-03-07T10:00:00Z",
                "expiresAt": "2026-03-07T10:05:00Z",
                "requiredSteps": 2,
                "status": "pending_stage2",
                "history": [],
            }
        )
        self.assertEqual(artifact.approvalId, "deadbeefcafe")
        self.assertEqual(artifact.requestedBy, "8241238117")
        self.assertEqual(artifact.stage, "pending_stage2")


if __name__ == "__main__":
    unittest.main()
