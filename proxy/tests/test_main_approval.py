import os
import unittest
from datetime import datetime, timezone

from app.google_calendar import _today_window
from app.main import _verify_approval_source


class ApprovalSourceBindingTests(unittest.TestCase):
    def test_same_user_and_chat_is_allowed(self) -> None:
        approval = {"requestedByUserId": "1001", "chatId": "2001"}
        ok, reason = _verify_approval_source(approval, user_id="1001", chat_id="2001")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_different_user_is_rejected(self) -> None:
        approval = {"requestedByUserId": "1001", "chatId": "2001"}
        ok, reason = _verify_approval_source(approval, user_id="1002", chat_id="2001")
        self.assertFalse(ok)
        self.assertEqual(reason, "approval_user_mismatch")

    def test_different_chat_is_rejected(self) -> None:
        approval = {"requestedByUserId": "1001", "chatId": "2001"}
        ok, reason = _verify_approval_source(approval, user_id="1001", chat_id="2002")
        self.assertFalse(ok)
        self.assertEqual(reason, "approval_chat_mismatch")


class GoogleCalendarWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_timezone = os.environ.get("GOOGLE_CALENDAR_TIMEZONE")
        os.environ["GOOGLE_CALENDAR_TIMEZONE"] = "Asia/Seoul"

    def tearDown(self) -> None:
        if self.old_timezone is None:
            os.environ.pop("GOOGLE_CALENDAR_TIMEZONE", None)
        else:
            os.environ["GOOGLE_CALENDAR_TIMEZONE"] = self.old_timezone

    def test_today_window_uses_local_timezone_and_returns_utc_iso(self) -> None:
        time_min, time_max = _today_window()
        parsed_min = datetime.fromisoformat(time_min.replace("Z", "+00:00"))
        parsed_max = datetime.fromisoformat(time_max.replace("Z", "+00:00"))

        self.assertEqual(parsed_min.tzinfo, timezone.utc)
        self.assertEqual(parsed_max.tzinfo, timezone.utc)
        self.assertLess(parsed_min, parsed_max)
        # Asia/Seoul local midnight should map to 15:00 UTC of the previous day.
        self.assertEqual(parsed_min.hour, 15)
        self.assertEqual(parsed_min.minute, 0)


if __name__ == "__main__":
    unittest.main()
