import json
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import app.telegram_poller as telegram_poller


class TelegramPollerTests(unittest.TestCase):
    def test_record_dead_letter_appends_jsonl_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dead_letter_path = Path(tmp_dir) / "dead-letter.jsonl"
            with patch.object(telegram_poller, "DEAD_LETTER_PATH", dead_letter_path):
                telegram_poller._record_dead_letter(
                    update={"update_id": 42, "message": {"text": "hello"}},
                    status=403,
                    detail="forbidden",
                )

            lines = dead_letter_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["updateId"], 42)
            self.assertEqual(payload["status"], 403)
            self.assertEqual(payload["detail"], "forbidden")

    def test_forward_update_retries_on_rate_limit(self) -> None:
        with patch("app.telegram_poller.urllib.request.urlopen") as mocked:
            mocked.side_effect = urllib.error.HTTPError(
                url="http://example.test",
                code=429,
                msg="Too Many Requests",
                hdrs=None,
                fp=io.BytesIO(b"rate limit"),
            )
            outcome, status, detail = telegram_poller._forward_update({"update_id": 1})

        self.assertEqual(outcome, "retry")
        self.assertEqual(status, 429)
        self.assertIn("rate limit", detail)

    def test_forward_update_dead_letters_permanent_client_errors(self) -> None:
        with patch("app.telegram_poller.urllib.request.urlopen") as mocked:
            mocked.side_effect = urllib.error.HTTPError(
                url="http://example.test",
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=io.BytesIO(b"forbidden"),
            )
            outcome, status, detail = telegram_poller._forward_update({"update_id": 2})

        self.assertEqual(outcome, "dead_letter")
        self.assertEqual(status, 403)
        self.assertIn("forbidden", detail)


if __name__ == "__main__":
    unittest.main()
