import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import private_164824_final_nav_backfill as replay


class FinalNAVReplayTests(unittest.TestCase):
    def test_final_nav_preserves_static_sleeve(self) -> None:
        self.assertAlmostEqual(
            replay.final_nav(1.0, 0.8937, 0.1063, 100.0, 110.0, 7.0, 7.0),
            1.08937,
        )

    def test_final_nav_applies_fx_to_risk_sleeve_only(self) -> None:
        self.assertAlmostEqual(
            replay.final_nav(1.0, 0.9, 0.1, 100.0, 100.0, 7.0, 7.07),
            1.009,
        )

    def test_completed_target_days_excludes_current_shanghai_day(self) -> None:
        values = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]
        now = datetime(2026, 8, 5, 9, 20, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.assertEqual(replay.completed_target_days(values, now), values[:2])


if __name__ == "__main__":
    unittest.main()
