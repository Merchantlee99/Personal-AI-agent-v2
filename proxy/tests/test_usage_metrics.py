import json
import tempfile
import unittest
from pathlib import Path

import app.main as main


class UsageMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_metrics_path = main.METRICS_STORE_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        main.METRICS_STORE_PATH = str(Path(self.temp_dir.name) / "llm_usage_metrics.json")

    def tearDown(self) -> None:
        main.METRICS_STORE_PATH = self.original_metrics_path
        self.temp_dir.cleanup()

    def test_record_usage_tracks_daily_counters(self) -> None:
        main._record_usage(
            agent_id="clio",
            configured_model="gemini-2.0-flash-lite",
            selected_model="gemini-2.5-flash",
            status="success",
            quota_429_hits=1,
        )

        path = Path(main.METRICS_STORE_PATH)
        self.assertTrue(path.is_file())

        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("daily", data)
        self.assertEqual(len(data["daily"]), 1)

        entry = next(iter(data["daily"].values()))
        self.assertEqual(entry["total"], 1)
        self.assertEqual(entry["success"], 1)
        self.assertEqual(entry["quota_429"], 1)
        self.assertEqual(entry["fallback_applied"], 1)
        self.assertEqual(entry["per_agent"]["clio"], 1)
        self.assertEqual(entry["per_model"]["gemini-2.5-flash"], 1)


if __name__ == "__main__":
    unittest.main()
