#!/usr/bin/env python3
"""Unit tests for the cache-only SZ164824 redemption-arbitrage input job."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import cache_private_164824_redemption_arbitrage_inputs as cache
import private_164824_valuation_uploader as india


class CacheInputTests(unittest.TestCase):
    def test_six_months_before_preserves_or_clamps_day(self) -> None:
        self.assertEqual(cache.six_months_before(date(2026, 8, 4)), date(2026, 2, 4))
        self.assertEqual(cache.six_months_before(date(2026, 8, 31)), date(2026, 2, 28))

    def test_china_weekdays_excludes_weekend(self) -> None:
        self.assertEqual(
            cache.china_weekdays(date(2026, 8, 1), date(2026, 8, 4)),
            [date(2026, 8, 3), date(2026, 8, 4)],
        )

    def test_progress_pending_counts_only_unrecorded_days(self) -> None:
        days = [date(2026, 8, 3), date(2026, 8, 4)]
        status = cache.progress_payload(
            start=days[0], end=days[-1], planned=days,
            entries={"2026-08-03": {"state": "complete"}, "2026-08-04": {"state": "error"}},
            started_at="2026-08-05T09:00:00+08:00", finished=True,
        )
        self.assertEqual(status["complete"], 1)
        self.assertEqual(status["errors"], 1)
        self.assertEqual(status["pending"], 1)

    def test_missing_payload_segments_identifies_empty_us_session(self) -> None:
        payload = {
            "bridge": {"common_minutes": 3},
            "nifty_china_session": {"quotes": {"09:30": {"bid": 1}}},
            "us_session": {"inda": [], "nifty": [{"bid": 1}]},
        }
        self.assertEqual(cache.missing_payload_segments(payload), ["us_inda"])

    def test_gzip_write_is_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "one.json.gz"
            cache.atomic_write_gzip_json(path, {"ok": True})
            import gzip
            self.assertEqual(gzip.open(path, "rt", encoding="utf-8").read().strip(), '{"ok":true}')

    def test_bridge_window_uses_new_york_close(self) -> None:
        start, end = cache.bridge_windows(date(2026, 8, 4))
        self.assertEqual((start.hour, start.minute), (15, 49))
        self.assertEqual((end.hour, end.minute), (15, 51))
        self.assertEqual(start.tzinfo, india.NEW_YORK)


if __name__ == "__main__":
    unittest.main()
