import json
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
