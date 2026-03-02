import unittest

from pydantic import ValidationError

from app.models import AgentRequest


class AgentRequestModelTests(unittest.TestCase):
    def test_history_accepts_text_field(self) -> None:
        payload = AgentRequest.model_validate(
            {
                "agent_id": "minerva",
                "message": "hello",
                "history": [{"role": "assistant", "text": "ok", "at": "2026-03-01T12:00:00Z"}],
            }
        )
        self.assertEqual(payload.history[0].text, "ok")

    def test_history_accepts_legacy_content_field(self) -> None:
        payload = AgentRequest.model_validate(
            {
                "agent_id": "minerva",
                "message": "hello",
                "history": [{"role": "user", "content": "legacy"}],
            }
        )
        self.assertEqual(payload.history[0].text, "legacy")

    def test_history_requires_content_value(self) -> None:
        with self.assertRaises(ValidationError):
            AgentRequest.model_validate(
                {
                    "agent_id": "minerva",
                    "message": "hello",
                    "history": [{"role": "user"}],
                }
            )


if __name__ == "__main__":
    unittest.main()
