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
                        "agent_id": "owl",
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
            self.assertIsNotNone(outbox_payload["verified_file"])

            vault_path = root / outbox_payload["vault_file"]
            vault_body = vault_path.read_text(encoding="utf-8")
            self.assertIn("## Clio Metadata", vault_body)
            self.assertIn("## Clio Links", vault_body)
            self.assertIn("https://example.com/report", vault_body)

            verified_files = sorted(verified.glob("*.json"))
            self.assertEqual(len(verified_files), 1)
            verified_payload = json.loads(verified_files[0].read_text(encoding="utf-8"))
            self.assertEqual(verified_payload["agent_id"], "clio")
            self.assertTrue(verified_payload["notebooklm"]["ready"])
            self.assertIn("#clio", verified_payload["tags"])

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
            self.assertIsNone(outbox_payload["verified_file"])

            vault_path = root / outbox_payload["vault_file"]
            vault_body = vault_path.read_text(encoding="utf-8")
            self.assertNotIn("## Clio Metadata", vault_body)
            self.assertEqual(len(list(verified.glob("*.json"))), 0)


if __name__ == "__main__":
    unittest.main()
