#!/usr/bin/env python3
"""Mock tests for the local-only IBKR BID/ASK batch cache."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_china_internet_ib_bidask_batch as batch  # noqa: E402
import private_china_internet_valuation_uploader as valuation  # noqa: E402


class BatchIBBidAskTest(unittest.TestCase):
    def test_mock_fetch_is_once_per_unique_component_and_side(self) -> None:
        shared = valuation.Component("0700", "Tencent", "HK", "HKD", 1.0)
        us = valuation.Component("BABA", "Alibaba", "US", "USD", 1.0)
        calls: list[tuple[str, bool]] = []

        def fake_fetch(_ib, _contract, *, what, use_rth, days, request_delay):
            self.assertEqual(tuple(days), (date(2026, 7, 9), date(2026, 7, 10)))
            self.assertEqual(request_delay, 0)
            calls.append((what, use_rth))
            return {(date(2026, 7, 10), "09:30"): 100.0 if what == "BID" else 100.1}

        with tempfile.TemporaryDirectory() as directory:
            result = batch.pull_bid_ask(None, (shared, shared, us), (date(2026, 7, 9), date(2026, 7, 10)), Path(directory), 0, fetch_series=fake_fetch)
            self.assertEqual(len(result), 4)
            self.assertEqual(len(calls), 4)
            self.assertEqual(calls.count(("BID", True)), 1)
            self.assertEqual(calls.count(("ASK", True)), 1)
            self.assertEqual(calls.count(("BID", False)), 1)
            self.assertEqual(calls.count(("ASK", False)), 1)
            path = Path(directory) / "series" / "HK_0700_BID.csv"
            with path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["price"], "100")
            self.assertEqual(rows[0]["source"], "IBKR_TWS_HISTORICAL_BID_ASK")


if __name__ == "__main__":
    unittest.main()
