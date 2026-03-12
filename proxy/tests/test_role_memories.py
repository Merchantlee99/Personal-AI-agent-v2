import unittest

from app.orch_store import render_clio_knowledge_memory_context, render_hermes_evidence_memory_context


class RoleMemoryContextTests(unittest.TestCase):
    def test_render_clio_knowledge_memory_context_includes_recent_notes(self) -> None:
        context = render_clio_knowledge_memory_context(
            {
                "schemaVersion": 1,
                "projects": ["TripPixel", "NanoClaw"],
                "mocs": ["[[TripPixel MOC]]", "[[AI PM 연구]]"],
                "recentNotes": [
                    {
                        "title": "GA4 이벤트 설계 원칙",
                        "type": "knowledge",
                        "folder": "01-Knowledge",
                        "templateName": "tpl-knowledge.md",
                        "vaultFile": "obsidian_vault/01-Knowledge/ga4-event-design.md",
                        "draftState": "draft",
                    }
                ],
                "dedupeCandidates": [
                    {
                        "title": "GA4 이벤트 설계 원칙",
                        "vaultFile": "obsidian_vault/01-Knowledge/ga4-event-design.md",
                        "relatedNotes": ["[[GA4 이벤트 네이밍]]"],
                    }
                ],
            }
        )
        self.assertIsNotNone(context)
        assert context is not None
        self.assertIn("Registered projects: TripPixel, NanoClaw", context)
        self.assertIn("GA4 이벤트 설계 원칙", context)
        self.assertIn("Dedupe candidates", context)

    def test_render_hermes_evidence_memory_context_filters_topic(self) -> None:
        context = render_hermes_evidence_memory_context(
            {
                "schemaVersion": 1,
                "topics": [
                    {
                        "topicKey": "robotaxi",
                        "title": "Robotaxi regulation shift",
                        "trustScore": 0.88,
                        "lastPriority": "high",
                        "lastDecision": "send_now",
                        "sourceDomains": ["domain/mobility"],
                    },
                    {
                        "topicKey": "llm-pricing",
                        "title": "LLM pricing update",
                        "trustScore": 0.42,
                        "lastPriority": "normal",
                        "lastDecision": "queue_digest",
                        "sourceDomains": ["domain/ai"],
                    },
                ],
            },
            topic_key="robotaxi",
        )
        self.assertIsNotNone(context)
        assert context is not None
        self.assertIn("Robotaxi regulation shift", context)
        self.assertNotIn("LLM pricing update", context)


if __name__ == "__main__":
    unittest.main()
