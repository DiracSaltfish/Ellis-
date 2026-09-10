from datetime import date, datetime
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze_private_164824_nifty_inda_bridge as bridge


class NiftyIndaBridgeAnalysisTests(unittest.TestCase):
    def test_fixed_beijing_windows_match_summer_us_close_then_open(self) -> None:
        start, end = bridge.china_windows(date(2026, 7, 31))
        self.assertEqual(start[0].isoformat(), "2026-07-31T03:49:00+08:00")
        self.assertEqual(start[0].astimezone(bridge.india.NEW_YORK).isoformat(), "2026-07-30T15:49:00-04:00")
        self.assertEqual(end[1].astimezone(bridge.india.NEW_YORK).isoformat(), "2026-07-31T09:41:00-04:00")

    def test_error_metrics_are_in_basis_points(self) -> None:
        metrics = bridge.error_metrics([0.001, -0.002])
        self.assertEqual(metrics["n"], 2)
        self.assertAlmostEqual(metrics["mean_bps"], -5.0)
        self.assertAlmostEqual(metrics["mae_bps"], 15.0)

    def test_rolling_beta_never_uses_current_day(self) -> None:
        rows = []
        for index in range(3):
            rows.append(bridge.Observation(
                china_day=f"2026-07-0{index + 1}", nifty_contract="NIFTYQ26",
                start_inda_mid=1, end_inda_mid=1, start_nifty_mid=1, end_nifty_mid=1,
                inda_return=0.01 * (index + 1), nifty_return=0.01 * (index + 1), raw_tracking_error=0,
                inda_start_spread_bps=1, inda_end_spread_bps=1, nifty_start_spread_bps=1, nifty_end_spread_bps=1,
                start_common_minutes=3, end_common_minutes=3,
            ))
        result = bridge.rolling_beta_errors(rows, 2)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["rolling_beta"], 1.0)


if __name__ == "__main__":
    unittest.main()
