import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import app.orch_store as orch_store


class ClioClaimReviewTests(unittest.TestCase):
    def test_confirm_clio_claim_review_updates_queue_memory_and_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_dir = root / "shared_memory"
            memory_dir.mkdir(parents=True, exist_ok=True)

            vault_file = "obsidian_vault/knowledge-note.md"
            note_path = root / vault_file
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(
                "\n".join(
                    [
                        "---",
                        'title: "테스트 지식 노트"',
                        'type: "knowledge"',
                        'draft_state: "draft"',
                        'updated: "2026-03-08"',
                        "---",
                        "",
                        "## 핵심 주장",
                        "PM은 측정 가능한 학습 루프를 설계해야 한다.",
                    ]
                ),
                encoding="utf-8",
            )

            (memory_dir / "clio_claim_review_queue.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "updatedAt": "2026-03-08T00:00:00Z",
                        "items": [
                            {
                                "id": "review123456",
                                "status": "pending_user_review",
                                "title": "테스트 지식 노트",
                                "topicKey": "pm-learning-loop",
                                "vaultFile": vault_file,
                                "requestedAt": "2026-03-08T00:00:00Z",
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (memory_dir / "clio_knowledge_memory.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "updatedAt": "2026-03-08T00:00:00Z",
                        "recentNotes": [
                            {
                                "title": "테스트 지식 노트",
                                "type": "knowledge",
                                "folder": "01-Knowledge",
                                "templateName": "tpl-knowledge.md",
                                "vaultFile": vault_file,
                                "tags": ["type/knowledge", "domain/pm"],
                                "projectLinks": ["[[NanoClaw]]"],
                                "mocCandidates": ["[[PM 스킬 맵]]"],
                                "relatedNotes": [],
                                "draftState": "draft",
                                "claimReviewRequired": True,
                                "claimReviewId": "review123456",
                                "updatedAt": "2026-03-08T00:00:00Z",
                            }
                        ],
                        "dedupeCandidates": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with (
                patch.object(orch_store, "ROOT", root),
                patch.object(orch_store, "MEMORY_DIR", memory_dir),
                patch.object(orch_store, "CLIO_CLAIM_REVIEW_QUEUE_FILE", memory_dir / "clio_claim_review_queue.json"),
                patch.object(orch_store, "CLIO_KNOWLEDGE_MEMORY_FILE", memory_dir / "clio_knowledge_memory.json"),
            ):
                updated = orch_store.confirm_clio_claim_review("review123456", "8241238117")

            self.assertIsNotNone(updated)
            self.assertEqual(updated["status"], "confirmed_by_user")
            self.assertEqual(updated["confirmedByUserId"], "8241238117")

            queue = json.loads((memory_dir / "clio_claim_review_queue.json").read_text(encoding="utf-8"))
            self.assertEqual(queue["items"][0]["status"], "confirmed_by_user")

            memory = json.loads((memory_dir / "clio_knowledge_memory.json").read_text(encoding="utf-8"))
            self.assertEqual(memory["recentNotes"][0]["draftState"], "confirmed")
            self.assertFalse(memory["recentNotes"][0]["claimReviewRequired"])

            body = note_path.read_text(encoding="utf-8")
            self.assertIn('draft_state: "confirmed"', body)

    def test_apply_clio_note_suggestion_updates_target_note_and_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_dir = root / "shared_memory"
            memory_dir.mkdir(parents=True, exist_ok=True)

            draft_file = "obsidian_vault/01-Knowledge/새 PM 루프 노트.md"
            draft_path = root / draft_file
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(
                "\n".join(
                    [
                        "---",
                        'title: "새 PM 루프 노트"',
                        'type: "knowledge"',
                        'draft_state: "draft"',
                        'updated: "2026-03-08"',
                        "---",
                        "",
                        "## 핵심 주장",
                        "측정과 회고는 하나의 루프로 관리해야 한다.",
                    ]
                ),
                encoding="utf-8",
            )

            target_file = "obsidian_vault/01-Knowledge/PM 학습 루프.md"
            target_path = root / target_file
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(
                "\n".join(
                    [
                        "---",
                        'title: "PM 학습 루프"',
                        'type: "knowledge"',
                        'draft_state: "confirmed"',
                        'updated: "2026-03-08"',
                        "---",
                        "",
                        "## 핵심 주장",
                        "기존 노트",
                    ]
                ),
                encoding="utf-8",
            )

            (memory_dir / "clio_knowledge_memory.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "updatedAt": "2026-03-08T00:00:00Z",
                        "recentNotes": [
                            {
                                "title": "새 PM 루프 노트",
                                "type": "knowledge",
                                "folder": "01-Knowledge",
                                "templateName": "tpl-knowledge.md",
                                "vaultFile": draft_file,
                                "tags": ["type/knowledge", "domain/pm"],
                                "projectLinks": ["[[TripPixel]]"],
                                "mocCandidates": ["[[PM 스킬 맵]]"],
                                "relatedNotes": ["[[PM 학습 루프]]"],
                                "draftState": "draft",
                                "claimReviewRequired": False,
                                "claimReviewId": "",
                                "noteAction": "update_candidate",
                                "updateTarget": "[[PM 학습 루프]]",
                                "updateTargetPath": target_file,
                                "mergeCandidates": [],
                                "mergeCandidatePaths": [],
                                "suggestionState": "pending",
                                "updatedAt": "2026-03-08T00:00:00Z",
                            }
                        ],
                        "dedupeCandidates": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with (
                patch.object(orch_store, "ROOT", root),
                patch.object(orch_store, "MEMORY_DIR", memory_dir),
                patch.object(orch_store, "CLIO_KNOWLEDGE_MEMORY_FILE", memory_dir / "clio_knowledge_memory.json"),
            ):
                suggestion_id = orch_store._make_clio_note_suggestion_id(draft_file)
                applied = orch_store.apply_clio_note_suggestion(suggestion_id, "8241238117")

            self.assertIsNotNone(applied)
            self.assertEqual(applied["appliedByUserId"], "8241238117")
            self.assertEqual(applied["noteAction"], "update_candidate")
            self.assertEqual(applied["appliedPaths"], [str(target_path)])

            updated_target = target_path.read_text(encoding="utf-8")
            self.assertIn("## Clio Suggested Update", updated_target)
            self.assertIn(f"<!-- clio-suggestion:{suggestion_id} -->", updated_target)
            self.assertIn("- source_draft: [[새 PM 루프 노트]]", updated_target)

            updated_draft = draft_path.read_text(encoding="utf-8")
            self.assertIn('draft_state: "review"', updated_draft)

            memory = json.loads((memory_dir / "clio_knowledge_memory.json").read_text(encoding="utf-8"))
            self.assertEqual(memory["recentNotes"][0]["draftState"], "review")
            self.assertEqual(memory["recentNotes"][0]["suggestionState"], "approved")

    def test_dismissed_suggestion_is_suppressed_until_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_dir = root / "shared_memory"
            memory_dir.mkdir(parents=True, exist_ok=True)

            draft_file = "obsidian_vault/01-Knowledge/새 PM 루프 노트.md"
            (root / draft_file).parent.mkdir(parents=True, exist_ok=True)
            (root / draft_file).write_text("body", encoding="utf-8")

            (memory_dir / "clio_knowledge_memory.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "updatedAt": "2026-03-08T00:00:00Z",
                        "recentNotes": [
                            {
                                "title": "새 PM 루프 노트",
                                "type": "knowledge",
                                "folder": "01-Knowledge",
                                "templateName": "tpl-knowledge.md",
                                "vaultFile": draft_file,
                                "tags": ["type/knowledge", "domain/pm"],
                                "projectLinks": ["[[TripPixel]]"],
                                "mocCandidates": ["[[PM 스킬 맵]]"],
                                "relatedNotes": ["[[PM 학습 루프]]"],
                                "draftState": "draft",
                                "claimReviewRequired": False,
                                "claimReviewId": "",
                                "noteAction": "update_candidate",
                                "updateTarget": "[[PM 학습 루프]]",
                                "updateTargetPath": "obsidian_vault/01-Knowledge/PM 학습 루프.md",
                                "mergeCandidates": [],
                                "mergeCandidatePaths": [],
                                "suggestionState": "pending",
                                "suggestionScore": 0.92,
                                "suggestionReasons": ["제목이 기존 노트와 정확히 일치합니다."],
                                "updatedAt": "2026-03-08T00:00:00Z",
                            }
                        ],
                        "dedupeCandidates": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with (
                patch.object(orch_store, "ROOT", root),
                patch.object(orch_store, "MEMORY_DIR", memory_dir),
                patch.object(orch_store, "CLIO_KNOWLEDGE_MEMORY_FILE", memory_dir / "clio_knowledge_memory.json"),
                patch.object(orch_store, "CLIO_SUGGESTION_DISMISS_COOLDOWN_SEC", 3600),
            ):
                suggestion_id = orch_store._make_clio_note_suggestion_id(draft_file)
                dismissed = orch_store.dismiss_clio_note_suggestion(suggestion_id, "8241238117")
                pending = orch_store.list_pending_clio_note_suggestions(limit=10)

            self.assertIsNotNone(dismissed)
            self.assertEqual(pending, [])
            memory = json.loads((memory_dir / "clio_knowledge_memory.json").read_text(encoding="utf-8"))
            self.assertEqual(memory["recentNotes"][0]["suggestionState"], "dismissed")
            self.assertTrue(memory["recentNotes"][0]["suggestionCooldownUntil"])

    def test_dismissed_suggestion_reappears_when_note_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_dir = root / "shared_memory"
            memory_dir.mkdir(parents=True, exist_ok=True)

            draft_file = "obsidian_vault/01-Knowledge/새 PM 루프 노트.md"
            (root / draft_file).parent.mkdir(parents=True, exist_ok=True)
            (root / draft_file).write_text("body", encoding="utf-8")

            memory_path = memory_dir / "clio_knowledge_memory.json"
            memory_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "updatedAt": "2026-03-08T00:00:00Z",
                        "recentNotes": [
                            {
                                "title": "새 PM 루프 노트",
                                "type": "knowledge",
                                "folder": "01-Knowledge",
                                "templateName": "tpl-knowledge.md",
                                "vaultFile": draft_file,
                                "draftState": "draft",
                                "claimReviewRequired": False,
                                "claimReviewId": "",
                                "noteAction": "merge_candidate",
                                "updateTarget": "",
                                "updateTargetPath": "",
                                "mergeCandidates": ["[[PM 학습 루프]]"],
                                "mergeCandidatePaths": ["obsidian_vault/01-Knowledge/PM 학습 루프.md"],
                                "suggestionState": "pending",
                                "suggestionScore": 0.71,
                                "suggestionReasons": ["병합 검토 가치가 높습니다."],
                                "updatedAt": "2026-03-08T00:00:00Z",
                            }
                        ],
                        "dedupeCandidates": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with (
                patch.object(orch_store, "ROOT", root),
                patch.object(orch_store, "MEMORY_DIR", memory_dir),
                patch.object(orch_store, "CLIO_KNOWLEDGE_MEMORY_FILE", memory_path),
                patch.object(orch_store, "CLIO_SUGGESTION_DISMISS_COOLDOWN_SEC", 3600),
            ):
                suggestion_id = orch_store._make_clio_note_suggestion_id(draft_file)
                dismissed = orch_store.dismiss_clio_note_suggestion(suggestion_id, "8241238117")
                self.assertIsNotNone(dismissed)

                memory = json.loads(memory_path.read_text(encoding="utf-8"))
                dismissed_at = datetime.fromisoformat(memory["recentNotes"][0]["dismissedAt"].replace("Z", "+00:00"))
                memory["recentNotes"][0]["mergeCandidatePaths"] = [
                    "obsidian_vault/01-Knowledge/PM 학습 루프.md",
                    "obsidian_vault/01-Knowledge/회고 루프.md",
                ]
                memory["recentNotes"][0]["updatedAt"] = (dismissed_at + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
                memory_path.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")

                pending = orch_store.list_pending_clio_note_suggestions(limit=10)

            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["suggestionState"], "pending")

    def test_approved_suggestion_cannot_be_applied_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_dir = root / "shared_memory"
            memory_dir.mkdir(parents=True, exist_ok=True)

            draft_file = "obsidian_vault/01-Knowledge/새 PM 루프 노트.md"
            draft_path = root / draft_file
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text("---\ndraft_state: \"draft\"\n---\n\nbody", encoding="utf-8")
            target_file = "obsidian_vault/01-Knowledge/PM 학습 루프.md"
            target_path = root / target_file
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text("body", encoding="utf-8")

            (memory_dir / "clio_knowledge_memory.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "updatedAt": "2026-03-08T00:00:00Z",
                        "recentNotes": [
                            {
                                "title": "새 PM 루프 노트",
                                "type": "knowledge",
                                "folder": "01-Knowledge",
                                "templateName": "tpl-knowledge.md",
                                "vaultFile": draft_file,
                                "draftState": "draft",
                                "claimReviewRequired": False,
                                "claimReviewId": "",
                                "noteAction": "update_candidate",
                                "updateTarget": "[[PM 학습 루프]]",
                                "updateTargetPath": target_file,
                                "mergeCandidates": [],
                                "mergeCandidatePaths": [],
                                "suggestionState": "pending",
                                "updatedAt": "2026-03-08T00:00:00Z",
                            }
                        ],
                        "dedupeCandidates": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with (
                patch.object(orch_store, "ROOT", root),
                patch.object(orch_store, "MEMORY_DIR", memory_dir),
                patch.object(orch_store, "CLIO_KNOWLEDGE_MEMORY_FILE", memory_dir / "clio_knowledge_memory.json"),
            ):
                suggestion_id = orch_store._make_clio_note_suggestion_id(draft_file)
                first = orch_store.apply_clio_note_suggestion(suggestion_id, "8241238117")
                second = orch_store.apply_clio_note_suggestion(suggestion_id, "8241238117")

            self.assertIsNotNone(first)
            self.assertIsNone(second)

    def test_suggestion_alert_is_not_repeated_until_fingerprint_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_dir = root / "shared_memory"
            memory_dir.mkdir(parents=True, exist_ok=True)

            draft_file = "obsidian_vault/01-Knowledge/새 PM 루프 노트.md"
            memory_path = memory_dir / "clio_knowledge_memory.json"
            alert_state_path = memory_dir / "clio_alert_state.json"
            memory_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "updatedAt": "2026-03-08T00:00:00Z",
                        "recentNotes": [
                            {
                                "title": "새 PM 루프 노트",
                                "type": "knowledge",
                                "folder": "01-Knowledge",
                                "templateName": "tpl-knowledge.md",
                                "vaultFile": draft_file,
                                "draftState": "draft",
                                "claimReviewRequired": False,
                                "noteAction": "merge_candidate",
                                "mergeCandidates": ["[[PM 학습 루프]]"],
                                "mergeCandidatePaths": ["obsidian_vault/01-Knowledge/PM 학습 루프.md"],
                                "suggestionState": "pending",
                                "updatedAt": "2026-03-08T00:00:00Z",
                            }
                        ],
                        "dedupeCandidates": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with (
                patch.object(orch_store, "ROOT", root),
                patch.object(orch_store, "MEMORY_DIR", memory_dir),
                patch.object(orch_store, "CLIO_KNOWLEDGE_MEMORY_FILE", memory_path),
                patch.object(orch_store, "CLIO_ALERT_STATE_FILE", alert_state_path),
            ):
                first = orch_store.list_new_clio_note_suggestion_alerts(limit=10)
                self.assertEqual(len(first), 1)
                orch_store.mark_clio_alert_sent(
                    "note_suggestion",
                    str(first[0]["id"]),
                    fingerprint=str(first[0]["suggestionFingerprint"]),
                )
                second = orch_store.list_new_clio_note_suggestion_alerts(limit=10)
                self.assertEqual(second, [])

                payload = json.loads(memory_path.read_text(encoding="utf-8"))
                payload["recentNotes"][0]["mergeCandidatePaths"].append("obsidian_vault/01-Knowledge/회고 루프.md")
                memory_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

                third = orch_store.list_new_clio_note_suggestion_alerts(limit=10)
                self.assertEqual(len(third), 1)


if __name__ == "__main__":
    unittest.main()
