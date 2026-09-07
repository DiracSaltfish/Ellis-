#!/usr/bin/env python3
"""Regression tests for SH513350's dated CFETS 16:30 spot-close cache."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_513350_spot_close as spot_close
import private_month_history_backfill as backfill


class SH513350SpotCloseTests(unittest.TestCase):
    def test_june_24_uses_the_verified_1630_spot_close(self) -> None:
        quote = spot_close.quote_for_day(date(2026, 6, 24))
        self.assertEqual(quote.rate, 6.8052)
        self.assertEqual(quote.quote_time, "16:30")
        self.assertEqual(quote.source, "CFETS_USD_CNY_SPOT_CLOSE_1630")

    def test_history_backfill_does_not_fall_back_to_hourly_reference_rate(self) -> None:
        quote = backfill.fx_for_day(date(2026, 6, 24), timeout=1)
        self.assertEqual(quote.rate, 6.8052)
        self.assertEqual(quote.source, "CFETS_USD_CNY_SPOT_CLOSE_1630")

    def test_missing_day_fails_closed(self) -> None:
        with self.assertRaises(spot_close.SpotCloseRateError):
            spot_close.quote_for_day(date(2026, 7, 13))


if __name__ == "__main__":
    unittest.main()
