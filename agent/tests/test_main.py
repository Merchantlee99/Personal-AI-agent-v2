from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "agents.json"
os.environ.setdefault("AGENT_CONFIG_PATH", str(FIXTURE_CONFIG))

from main import process_file


class AgentPipelineTests(unittest.TestCase):
    def _make_dirs(self, root: Path) -> tuple[Path, Path, Path, Path, Path]:
        inbox = root / "inbox"
        outbox = root / "outbox"
        archive = root / "archive"
        vault = root / "obsidian_vault"
        verified = root / "verified_inbox"
        for directory in (inbox, outbox, archive, vault, verified):
            directory.mkdir(parents=True, exist_ok=True)
        return inbox, outbox, archive, vault, verified

    def test_clio_pipeline_writes_tags_links_and_verified_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inbox, outbox, archive, vault, verified = self._make_dirs(root)
            (vault / "2026-03-02").mkdir(parents=True, exist_ok=True)
            (vault / "2026-03-02" / "trend-ai-overview.md").write_text("# seed note\n", encoding="utf-8")

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
            self.assertIn("#clio", outbox_payload["tags"])
            self.assertIn("[[Hermes-Daily-Briefing]]", outbox_payload["related_links"])
            self.assertIn("https://example.com/report", outbox_payload["source_urls"])
            self.assertEqual(outbox_payload["source_language"], "en")
            self.assertEqual(outbox_payload["deepl_target_lang"], "KO")
            self.assertTrue(outbox_payload["deepl_required"])
            self.assertFalse(outbox_payload["deepl_applied"])
            self.assertIsNotNone(outbox_payload["verified_file"])

            vault_path = root / outbox_payload["vault_file"]
            vault_body = vault_path.read_text(encoding="utf-8")
            self.assertIn("---", vault_body)
            self.assertIn("## Clio Metadata", vault_body)
            self.assertIn("## Clio Links", vault_body)
            self.assertIn("https://example.com/report", vault_body)
            self.assertIn('source_language: "en"', vault_body)
            self.assertIn("deepl_required: true", vault_body)

            verified_files = sorted(verified.glob("*.json"))
            self.assertEqual(len(verified_files), 1)
            verified_payload = json.loads(verified_files[0].read_text(encoding="utf-8"))
            self.assertEqual(verified_payload["agent_id"], "clio")
            self.assertTrue(verified_payload["notebooklm"]["ready"])
            self.assertIn("#clio", verified_payload["tags"])
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
            self.assertEqual(outbox_payload["tags"], [])
            self.assertEqual(outbox_payload["related_links"], [])
            self.assertEqual(outbox_payload["source_urls"], [])
            self.assertIsNone(outbox_payload["source_language"])
            self.assertFalse(outbox_payload["deepl_required"])
            self.assertFalse(outbox_payload["deepl_applied"])
            self.assertIsNone(outbox_payload["verified_file"])

            vault_path = root / outbox_payload["vault_file"]
            vault_body = vault_path.read_text(encoding="utf-8")
            self.assertNotIn("## Clio Metadata", vault_body)
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


if __name__ == "__main__":
    unittest.main()
