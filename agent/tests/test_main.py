from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "agents.json"
os.environ.setdefault("AGENT_CONFIG_PATH", str(FIXTURE_CONFIG))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import process_file, process_pending_files


class AgentPipelineTests(unittest.TestCase):
    def _make_dirs(self, root: Path) -> tuple[Path, Path, Path, Path, Path]:
        os.environ["SHARED_ROOT"] = str(root)
        inbox = root / "inbox"
        outbox = root / "outbox"
        archive = root / "archive"
        vault = root / "obsidian_vault"
        verified = root / "verified_inbox"
        for directory in (inbox, outbox, archive, vault, verified):
            directory.mkdir(parents=True, exist_ok=True)
        return inbox, outbox, archive, vault, verified

    def test_clio_pipeline_writes_template_driven_note_and_verified_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inbox, outbox, archive, vault, verified = self._make_dirs(root)
            (vault / "02-References").mkdir(parents=True, exist_ok=True)
            (vault / "02-References" / "trend-ai-overview.md").write_text("# seed note\n", encoding="utf-8")

            payload_file = inbox / "clio.json"
            payload_file.write_text(
                json.dumps(
                    {
                        "agent_id": "clio",
                        "source": "unit-test",
                        "message": "Hermes trend report for NotebookLM https://example.com/report",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            process_file(payload_file, inbox, outbox, archive, vault, verified)

            outbox_files = sorted(outbox.glob("*.json"))
            self.assertEqual(len(outbox_files), 1)
            outbox_payload = json.loads(outbox_files[0].read_text(encoding="utf-8"))
            self.assertEqual(outbox_payload["agent_id"], "clio")
            self.assertTrue(outbox_payload["notebooklm_ready"])
            self.assertEqual(outbox_payload["format_version"], "clio_obsidian_v2")
            self.assertEqual(outbox_payload["type"], "article")
            self.assertEqual(outbox_payload["folder"], "02-References")
            self.assertEqual(outbox_payload["template_name"], "tpl-article.md")
            self.assertEqual(outbox_payload["draft_state"], "draft")
            self.assertEqual(outbox_payload["note_action"], "create")
            self.assertIsNone(outbox_payload["update_target"])
            self.assertIsNone(outbox_payload["update_target_path"])
            self.assertEqual(outbox_payload["merge_candidates"], [])
            self.assertEqual(outbox_payload["merge_candidate_paths"], [])
            self.assertIsNone(outbox_payload["suggestion_score"])
            self.assertEqual(outbox_payload["suggestion_reasons"], [])
            self.assertGreaterEqual(outbox_payload["classification_confidence"], 0.55)
            self.assertIn("type/article", outbox_payload["tags"])
            self.assertIn("source/example", outbox_payload["tags"])
            self.assertIn("[[trend-ai-overview]]", outbox_payload["related_notes"])
            self.assertIn("https://example.com/report", outbox_payload["source_urls"])
            self.assertEqual(outbox_payload["source_language"], "en")
            self.assertEqual(outbox_payload["deepl_target_lang"], "KO")
            self.assertTrue(outbox_payload["deepl_required"])
            self.assertFalse(outbox_payload["deepl_applied"])
            self.assertIsNotNone(outbox_payload["verified_file"])

            vault_path = root / outbox_payload["vault_file"]
            vault_body = vault_path.read_text(encoding="utf-8")
            self.assertIn("---", vault_body)
            self.assertIn('clio_format_version: "clio_obsidian_v2"', vault_body)
            self.assertIn('type: "article"', vault_body)
            self.assertNotIn("# NanoClaw Inbox Capture", vault_body)
            self.assertIn("## 한 줄 요약", vault_body)
            self.assertIn("## 3가지 핵심 포인트", vault_body)
            self.assertIn("https://example.com/report", vault_body)
            self.assertNotIn("## Clio Metadata", vault_body)
            self.assertNotIn("## Clio Relationships", vault_body)
            self.assertNotIn("## NotebookLM Summary", vault_body)
            self.assertNotIn("## Routing Rules", vault_body)

            verified_files = sorted(verified.glob("*.json"))
            self.assertEqual(len(verified_files), 1)
            verified_payload = json.loads(verified_files[0].read_text(encoding="utf-8"))
            self.assertEqual(verified_payload["agent_id"], "clio")
            self.assertEqual(verified_payload["format_version"], "clio_obsidian_v2")
            self.assertTrue(verified_payload["notebooklm"]["ready"])
            self.assertEqual(verified_payload["type"], "article")
            self.assertEqual(verified_payload["folder"], "02-References")
            self.assertEqual(verified_payload["template_name"], "tpl-article.md")
            self.assertEqual(verified_payload["draft_state"], "draft")
            self.assertEqual(verified_payload["note_action"], "create")
            self.assertIsNone(verified_payload["update_target"])
            self.assertIsNone(verified_payload["update_target_path"])
            self.assertEqual(verified_payload["merge_candidates"], [])
            self.assertEqual(verified_payload["merge_candidate_paths"], [])
            self.assertIsNone(verified_payload["suggestion_score"])
            self.assertEqual(verified_payload["suggestion_reasons"], [])
            self.assertIn("type/article", verified_payload["tags"])
            self.assertIn("[[trend-ai-overview]]", verified_payload["related_notes"])
            self.assertIn("clio_format_version", verified_payload["frontmatter"])
            self.assertIn("https://example.com/report", verified_payload["source_urls"])
            self.assertEqual(verified_payload["source_language"], "en")
            self.assertTrue(verified_payload["deepl"]["required"])
            self.assertFalse(verified_payload["deepl"]["applied"])

            archive_files = sorted(archive.glob("*.json"))
            self.assertEqual(len(archive_files), 1)

    def test_non_clio_route_keeps_pipeline_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inbox, outbox, archive, vault, verified = self._make_dirs(root)

            payload_file = inbox / "hermes.json"
            payload_file.write_text(
                json.dumps(
                    {
                        "agent_id": "hermes",
                        "source": "unit-test",
                        "message": "daily trend capture",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            process_file(payload_file, inbox, outbox, archive, vault, verified)

            outbox_files = sorted(outbox.glob("*.json"))
            self.assertEqual(len(outbox_files), 1)
            outbox_payload = json.loads(outbox_files[0].read_text(encoding="utf-8"))
            self.assertEqual(outbox_payload["agent_id"], "hermes")
            self.assertFalse(outbox_payload["notebooklm_ready"])
            self.assertIsNone(outbox_payload["format_version"])
            self.assertEqual(outbox_payload["tags"], [])
            self.assertEqual(outbox_payload["project_links"], [])
            self.assertEqual(outbox_payload["moc_candidates"], [])
            self.assertEqual(outbox_payload["related_notes"], [])
            self.assertEqual(outbox_payload["source_urls"], [])
            self.assertIsNone(outbox_payload["source_language"])
            self.assertFalse(outbox_payload["deepl_required"])
            self.assertFalse(outbox_payload["deepl_applied"])
            self.assertIsNone(outbox_payload["verified_file"])

            vault_path = root / outbox_payload["vault_file"]
            vault_body = vault_path.read_text(encoding="utf-8")
            self.assertNotIn("## Clio Metadata", vault_body)
            self.assertIn("runtime_agent_notes", outbox_payload["vault_file"])
            self.assertEqual(len(list(verified.glob("*.json"))), 0)

    def test_hermes_deep_dive_creates_minerva_followup_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inbox, outbox, archive, vault, verified = self._make_dirs(root)

            previous = os.environ.get("HERMES_DEEP_DIVE_AUTO_MINERVA")
            os.environ["HERMES_DEEP_DIVE_AUTO_MINERVA"] = "true"
            try:
                payload_file = inbox / "hermes-deep-dive.json"
                payload_file.write_text(
                    json.dumps(
                        {
                            "agent_id": "hermes",
                            "source": "telegram-inline-action",
                            "message": "\n".join(
                                [
                                    "[trigger] telegram_inline_hermes_find_more",
                                    "[topic] mobility-market",
                                    "[title] 로보택시 시장 변화",
                                    "",
                                    "근거 수집 요청",
                                ]
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                process_file(payload_file, inbox, outbox, archive, vault, verified)
            finally:
                if previous is None:
                    os.environ.pop("HERMES_DEEP_DIVE_AUTO_MINERVA", None)
                else:
                    os.environ["HERMES_DEEP_DIVE_AUTO_MINERVA"] = previous

            outbox_files = sorted(outbox.glob("*.json"))
            self.assertEqual(len(outbox_files), 1)
            outbox_payload = json.loads(outbox_files[0].read_text(encoding="utf-8"))
            self.assertEqual(outbox_payload["agent_id"], "hermes")
            self.assertIn("followup_minerva_inbox", outbox_payload)

            followup_name = outbox_payload["followup_minerva_inbox"]
            followup_path = inbox / followup_name
            self.assertTrue(followup_path.exists())

            followup_payload = json.loads(followup_path.read_text(encoding="utf-8"))
            self.assertEqual(followup_payload["agent_id"], "minerva")
            self.assertEqual(followup_payload["source"], "agent-followup")
            self.assertIn("[trigger] hermes_deep_dive_auto_minerva_insight", followup_payload["message"])
            self.assertIn("deep_dive_vault_file:", followup_payload["message"])

    def test_knowledge_note_creates_claim_review_queue_and_clio_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inbox, outbox, archive, vault, verified = self._make_dirs(root)
            previous_shared_root = os.environ.get("SHARED_ROOT")
            os.environ["SHARED_ROOT"] = str(root)
            try:
                payload_file = inbox / "clio-knowledge.json"
                payload_file.write_text(
                    json.dumps(
                        {
                            "agent_id": "clio",
                            "source": "unit-test",
                            "message": "\n".join(
                                [
                                    "[title] PM은 측정 가능한 학습 루프를 설계해야 한다",
                                    "[topic] pm-learning-loop",
                                    "",
                                    "핵심 주장: PM은 측정 가능한 학습 루프를 설계해야 한다.",
                                    "왜 이렇게 생각하는가: 제품 개선은 측정 없이는 반복할 수 없기 때문이다.",
                                ]
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                process_file(payload_file, inbox, outbox, archive, vault, verified)
            finally:
                if previous_shared_root is None:
                    os.environ.pop("SHARED_ROOT", None)
                else:
                    os.environ["SHARED_ROOT"] = previous_shared_root

            outbox_payload = json.loads(next(outbox.glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(outbox_payload["type"], "knowledge")
            self.assertTrue(outbox_payload["claim_review_required"])
            self.assertTrue(outbox_payload["claim_review_id"])

            verified_payload = json.loads(next(verified.glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(verified_payload["type"], "knowledge")
            self.assertTrue(verified_payload["claim_review_required"])
            self.assertEqual(verified_payload["claim_review_id"], outbox_payload["claim_review_id"])

            queue_path = root / "shared_memory" / "clio_claim_review_queue.json"
            self.assertTrue(queue_path.exists())
            queue_payload = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(len(queue_payload["items"]), 1)
            self.assertEqual(queue_payload["items"][0]["status"], "pending_user_review")
            self.assertEqual(queue_payload["items"][0]["id"], outbox_payload["claim_review_id"])

            memory_path = root / "shared_memory" / "clio_knowledge_memory.json"
            self.assertTrue(memory_path.exists())
            memory_payload = json.loads(memory_path.read_text(encoding="utf-8"))
            self.assertEqual(memory_payload["recentNotes"][0]["type"], "knowledge")
            self.assertTrue(memory_payload["recentNotes"][0]["claimReviewRequired"])

    def test_unknown_agent_is_quarantined_instead_of_falling_back_to_minerva(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inbox, outbox, archive, vault, verified = self._make_dirs(root)

            payload_file = inbox / "unknown-agent.json"
            payload_file.write_text(
                json.dumps(
                    {
                        "agent_id": "owl",
                        "source": "unit-test",
                        "message": "legacy alias should not silently become minerva",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            process_pending_files(inbox, outbox, archive, vault, verified)

            self.assertEqual(list(outbox.glob("*.json")), [])
            self.assertEqual(list(verified.glob("*.json")), [])
            quarantine_files = list((archive / "quarantine").rglob("*unknown-agent.json"))
            self.assertEqual(len(quarantine_files), 1)
            error_sidecars = list((archive / "quarantine").rglob("*unknown-agent.json.error.json"))
            self.assertEqual(len(error_sidecars), 1)

    def test_clio_pipeline_marks_update_candidate_when_matching_note_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inbox, outbox, archive, vault, verified = self._make_dirs(root)
            (vault / "01-Knowledge").mkdir(parents=True, exist_ok=True)
            (vault / "01-Knowledge" / "PM 학습 루프.md").write_text("# existing\n", encoding="utf-8")

            payload_file = inbox / "clio-update.json"
            payload_file.write_text(
                json.dumps(
                    {
                        "agent_id": "clio",
                        "source": "unit-test",
                        "message": "\n".join(
                            [
                                "[title] PM 학습 루프",
                                "[topic] pm-learning-loop",
                                "",
                                "PM 학습 루프에 대한 개인 지식 정리",
                            ]
                        ),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            process_file(payload_file, inbox, outbox, archive, vault, verified)

            outbox_payload = json.loads(next(outbox.glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(outbox_payload["note_action"], "update_candidate")
            self.assertEqual(outbox_payload["update_target"], "[[PM 학습 루프]]")
            self.assertEqual(outbox_payload["update_target_path"], "obsidian_vault/01-Knowledge/PM 학습 루프.md")
            self.assertEqual(outbox_payload["merge_candidates"], [])
            self.assertEqual(outbox_payload["merge_candidate_paths"], [])
            self.assertGreaterEqual(outbox_payload["suggestion_score"], 0.9)
            self.assertGreaterEqual(len(outbox_payload["suggestion_reasons"]), 2)


if __name__ == "__main__":
    unittest.main()
