import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.llm_client import RetryableLLMError
from app.main import MODEL_FALLBACKS, MODEL_ROUTING, agent_reply
from app.models import AgentRequest


class AgentRoutingFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.routing_snapshot = dict(MODEL_ROUTING)
        self.fallback_snapshot = {key: list(value) for key, value in MODEL_FALLBACKS.items()}

    def tearDown(self) -> None:
        MODEL_ROUTING.clear()
        MODEL_ROUTING.update(self.routing_snapshot)
        MODEL_FALLBACKS.clear()
        MODEL_FALLBACKS.update({key: list(value) for key, value in self.fallback_snapshot.items()})

    def test_quota_429_falls_back_to_next_model(self) -> None:
        MODEL_ROUTING["clio"] = "gemini-2.0-flash-lite"
        MODEL_FALLBACKS["clio"] = ["gemini-2.5-flash"]

        def fake_generate(*, model: str, **_: object) -> str:
            if model == "gemini-2.0-flash-lite":
                raise RetryableLLMError("retryable http error: 429 quota exceeded")
            return "fallback reply"

        with patch("app.main.generate_agent_reply", side_effect=fake_generate):
            result = agent_reply(AgentRequest(agent_id="clio", message="test"), None)

        self.assertEqual(result.model, "gemini-2.5-flash")
        self.assertEqual(result.reply, "fallback reply")

    def test_non_quota_retryable_error_does_not_fallback(self) -> None:
        MODEL_ROUTING["clio"] = "gemini-2.0-flash-lite"
        MODEL_FALLBACKS["clio"] = ["gemini-2.5-flash"]

        with patch(
            "app.main.generate_agent_reply",
            side_effect=RetryableLLMError("retryable transport error: timeout"),
        ):
            with self.assertRaises(HTTPException) as raised:
                agent_reply(AgentRequest(agent_id="clio", message="test"), None)

        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("LLM transient failure", str(raised.exception.detail))


if __name__ == "__main__":
    unittest.main()
