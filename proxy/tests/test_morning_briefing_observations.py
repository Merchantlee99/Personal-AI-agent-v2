from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.orch_store import append_morning_briefing_observation


class MorningBriefingObservationTests(unittest.TestCase):
    def test_append_observation_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "morning_briefing_observations.jsonl"
            with patch("app.orch_store.MORNING_BRIEFING_OBSERVATIONS_FILE", target):
                append_morning_briefing_observation(
                    {
                        "eventId": "evt-1",
                        "topicKey": "morning-briefing",
                        "decision": "send_now",
                        "telegram": {"sent": True, "reason": "ok"},
                    }
                )

            lines = target.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["eventId"], "evt-1")
            self.assertEqual(payload["decision"], "send_now")
            self.assertTrue(payload["telegram"]["sent"])
            self.assertIn("observedAt", payload)


if __name__ == "__main__":
    unittest.main()
