import os
import unittest
from unittest.mock import patch

from app.llm_client import FatalLLMError, RetryableLLMError, generate_agent_reply


class LLMClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_snapshot = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.env_snapshot)

    def test_mock_provider_returns_mock_reply(self) -> None:
        os.environ["LLM_PROVIDER"] = "mock"
        reply = generate_agent_reply(
            agent_id="minerva",
            model="gemini-2.0-flash",
            role_boundary="orchestrates",
            message="hello",
            history=[],
        )
        self.assertIn("[Minerva 결정요약]", reply)
        self.assertIn("hello", reply)

    def test_auto_without_api_key_falls_back_to_mock(self) -> None:
        os.environ["LLM_PROVIDER"] = "auto"
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("GOOGLE_API_KEY", None)
        reply = generate_agent_reply(
            agent_id="clio",
            model="gemini-2.0-flash-lite",
            role_boundary="documentation",
            message="note me",
            history=[],
        )
        self.assertIn("[Clio 문서화요약]", reply)

    def test_gemini_provider_retries_then_succeeds(self) -> None:
        os.environ["LLM_PROVIDER"] = "gemini"
        os.environ["GEMINI_API_KEY"] = "test-key"
        os.environ["MODEL_MAX_RETRIES"] = "3"

        side_effects = [RetryableLLMError("tmp"), "real answer"]

        with patch("app.llm_client._call_gemini_once", side_effect=side_effects) as call_once:
            with patch("app.llm_client.time.sleep", return_value=None):
                reply = generate_agent_reply(
                    agent_id="hermes",
                    model="gemini-2.0-flash",
                    role_boundary="briefing",
                    message="trend",
                    history=[],
                )

        self.assertEqual(reply, "real answer")
        self.assertEqual(call_once.call_count, 2)

    def test_gemini_provider_requires_api_key(self) -> None:
        os.environ["LLM_PROVIDER"] = "gemini"
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("GOOGLE_API_KEY", None)

        with self.assertRaises(FatalLLMError):
            generate_agent_reply(
                agent_id="minerva",
                model="gemini-2.0-flash",
                role_boundary="orchestrates",
                message="key?",
                history=[],
            )

    def test_auto_falls_back_when_gemini_fails(self) -> None:
        os.environ["LLM_PROVIDER"] = "auto"
        os.environ["GEMINI_API_KEY"] = "test-key"

        with patch("app.llm_client._call_gemini_once", side_effect=FatalLLMError("bad")):
            reply = generate_agent_reply(
                agent_id="minerva",
                model="gemini-2.0-flash",
                role_boundary="orchestrates",
                message="fallback",
                history=[],
            )

        self.assertIn("[Minerva 결정요약]", reply)

    def test_anthropic_provider_retries_then_succeeds(self) -> None:
        os.environ["LLM_PROVIDER"] = "anthropic"
        os.environ["ANTHROPIC_API_KEY"] = "test-anthropic-key"
        os.environ["MODEL_MAX_RETRIES"] = "3"

        side_effects = [RetryableLLMError("tmp"), "anthropic answer"]

        with patch("app.llm_client._call_anthropic_once", side_effect=side_effects) as call_once:
            with patch("app.llm_client.time.sleep", return_value=None):
                reply = generate_agent_reply(
                    agent_id="minerva",
                    model="claude-sonnet-4-6",
                    role_boundary="orchestrates",
                    message="second-order insight",
                    history=[],
                )

        self.assertEqual(reply, "anthropic answer")
        self.assertEqual(call_once.call_count, 2)

    def test_anthropic_provider_requires_api_key(self) -> None:
        os.environ["LLM_PROVIDER"] = "anthropic"
        os.environ.pop("ANTHROPIC_API_KEY", None)

        with self.assertRaises(FatalLLMError):
            generate_agent_reply(
                agent_id="minerva",
                model="claude-sonnet-4-6",
                role_boundary="orchestrates",
                message="key?",
                history=[],
            )

    def test_auto_prefers_anthropic_for_claude_model(self) -> None:
        os.environ["LLM_PROVIDER"] = "auto"
        os.environ["ANTHROPIC_API_KEY"] = "test-anthropic-key"
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("GOOGLE_API_KEY", None)

        with patch("app.llm_client._call_anthropic_with_retry", return_value="claude reply") as anthropic_call:
            reply = generate_agent_reply(
                agent_id="minerva",
                model="claude-sonnet-4-6",
                role_boundary="orchestrates",
                message="insight",
                history=[],
            )

        self.assertEqual(reply, "claude reply")
        self.assertEqual(anthropic_call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
