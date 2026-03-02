import asyncio
import hashlib
import hmac
import os
import time
import unittest

from fastapi import HTTPException

from app.security import rate_limiter, replay_window, verify_internal_request


class DummyRequest:
    def __init__(self, headers: dict[str, str], body: str) -> None:
        self.headers = headers
        self._body = body.encode("utf-8")

    async def body(self) -> bytes:
        return self._body


class InternalSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_token = os.environ.get("INTERNAL_API_TOKEN")
        self.old_secret = os.environ.get("INTERNAL_SIGNING_SECRET")
        self.old_rate_limit = os.environ.get("INTERNAL_RATE_LIMIT_PER_MINUTE")
        os.environ["INTERNAL_API_TOKEN"] = "test-internal-token"
        os.environ["INTERNAL_SIGNING_SECRET"] = "test-signing-secret"
        os.environ["INTERNAL_RATE_LIMIT_PER_MINUTE"] = "120"
        replay_window.clear()
        rate_limiter.clear()

    def tearDown(self) -> None:
        replay_window.clear()
        rate_limiter.clear()
        if self.old_token is None:
            os.environ.pop("INTERNAL_API_TOKEN", None)
        else:
            os.environ["INTERNAL_API_TOKEN"] = self.old_token

        if self.old_secret is None:
            os.environ.pop("INTERNAL_SIGNING_SECRET", None)
        else:
            os.environ["INTERNAL_SIGNING_SECRET"] = self.old_secret

        if self.old_rate_limit is None:
            os.environ.pop("INTERNAL_RATE_LIMIT_PER_MINUTE", None)
        else:
            os.environ["INTERNAL_RATE_LIMIT_PER_MINUTE"] = self.old_rate_limit

    @staticmethod
    def _signature(secret: str, timestamp: str, nonce: str, body: str) -> str:
        payload = f"{timestamp}.{nonce}.{body}".encode("utf-8")
        return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    def _request(self, body: str, timestamp: str, nonce: str, signature: str) -> DummyRequest:
        headers = {
            "x-internal-token": "test-internal-token",
            "x-timestamp": timestamp,
            "x-nonce": nonce,
            "x-signature": signature,
        }
        return DummyRequest(headers=headers, body=body)

    @staticmethod
    def _run_verify(request: DummyRequest) -> HTTPException | None:
        try:
            asyncio.run(verify_internal_request(request))  # type: ignore[arg-type]
        except HTTPException as exc:
            return exc
        return None

    def test_invalid_signature_does_not_consume_nonce(self) -> None:
        body = '{"agent_id":"minerva","message":"security-check","history":[],"source":"web"}'
        timestamp = str(int(time.time()))
        nonce = "nonce-invalid-first"

        invalid_request = self._request(body=body, timestamp=timestamp, nonce=nonce, signature="bad-signature")
        invalid_error = self._run_verify(invalid_request)
        self.assertIsNotNone(invalid_error)
        self.assertEqual(invalid_error.status_code, 401)
        self.assertEqual(invalid_error.detail, "Invalid signature")

        valid_signature = self._signature("test-signing-secret", timestamp, nonce, body)
        valid_request = self._request(body=body, timestamp=timestamp, nonce=nonce, signature=valid_signature)
        valid_error = self._run_verify(valid_request)
        self.assertIsNone(valid_error)

    def test_replay_still_blocks_after_valid_request(self) -> None:
        body = '{"agent_id":"clio","message":"replay-check","history":[],"source":"web"}'
        timestamp = str(int(time.time()))
        nonce = "nonce-replay-check"
        signature = self._signature("test-signing-secret", timestamp, nonce, body)

        first_request = self._request(body=body, timestamp=timestamp, nonce=nonce, signature=signature)
        first_error = self._run_verify(first_request)
        self.assertIsNone(first_error)

        replay_request = self._request(body=body, timestamp=timestamp, nonce=nonce, signature=signature)
        replay_error = self._run_verify(replay_request)
        self.assertIsNotNone(replay_error)
        self.assertEqual(replay_error.status_code, 409)
        self.assertEqual(replay_error.detail, "Replay detected")

    def test_rate_limit_blocks_excess_requests(self) -> None:
        os.environ["INTERNAL_RATE_LIMIT_PER_MINUTE"] = "1"
        body = '{"agent_id":"hermes","message":"rate-limit-check","history":[],"source":"web"}'
        timestamp = str(int(time.time()))

        nonce_one = "nonce-rate-1"
        signature_one = self._signature("test-signing-secret", timestamp, nonce_one, body)
        first_request = self._request(body=body, timestamp=timestamp, nonce=nonce_one, signature=signature_one)
        first_error = self._run_verify(first_request)
        self.assertIsNone(first_error)

        nonce_two = "nonce-rate-2"
        signature_two = self._signature("test-signing-secret", timestamp, nonce_two, body)
        second_request = self._request(body=body, timestamp=timestamp, nonce=nonce_two, signature=signature_two)
        second_error = self._run_verify(second_request)
        self.assertIsNotNone(second_error)
        self.assertEqual(second_error.status_code, 429)
        self.assertEqual(second_error.detail, "Rate limit exceeded")


if __name__ == "__main__":
    unittest.main()
