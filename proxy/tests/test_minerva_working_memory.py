import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.orch_store as orch_store


class MinervaWorkingMemoryTests(unittest.TestCase):
    def test_set_and_get_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "minerva_working_memory.json"
            payload = {
                "identity": {
                    "preferredName": "이든",
                    "careerStage": "PM 취업 준비 중",
                },
                "careerTrajectory": {
                    "shortTerm": "IT PM 취업",
                    "midTerm": "Tech PM -> AI 제품 리더",
                    "longTerm": "35세 이전 CEO",
                },
                "activeProjects": [
                    {
                        "name": "TripPixel",
                        "stage": "launch-and-measure",
                        "priority": "highest",
                        "objective": "런칭 → 측정 → 개선 데이터 사이클 완성",
                        "facts": ["메인 포트폴리오", "GA4+BigQuery 설계 완료"],
                    }
                ],
            }
            with patch.object(orch_store, "MINERVA_WORKING_MEMORY_FILE", target):
                saved = orch_store.set_minerva_working_memory(payload)
                loaded = orch_store.get_minerva_working_memory()

            self.assertEqual(saved["identity"]["preferredName"], "이든")
            self.assertEqual(loaded["careerTrajectory"]["shortTerm"], "IT PM 취업")
            self.assertEqual(loaded["activeProjects"][0]["name"], "TripPixel")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_render_context_is_compact_and_informative(self) -> None:
        rendered = orch_store.render_minerva_working_memory_context(
            {
                "identity": {"preferredName": "이든", "careerStage": "PM 취업 준비 중"},
                "positioning": {
                    "thesis": "AI 시대 PM",
                    "strengths": ["자동화 우선", "데이터 기반 의사결정", "구조적 문서화"],
                },
                "activeProjects": [
                    {
                        "name": "NanoClaw",
                        "stage": "stabilization",
                        "priority": "high",
                        "objective": "Telegram 중심 운영 안정화",
                        "facts": ["멀티 에이전트", "실사용 가치 검증 중"],
                    }
                ],
                "currentGaps": ["TripPixel 실측 데이터 확보 필요"],
            },
            max_chars=1200,
        )

        assert rendered is not None
        self.assertIn("Preferred name: 이든", rendered)
        self.assertIn("Positioning: AI 시대 PM", rendered)
        self.assertIn("NanoClaw", rendered)
        self.assertIn("Current gaps:", rendered)
        self.assertLessEqual(len(rendered), 1200)


if __name__ == "__main__":
    unittest.main()
