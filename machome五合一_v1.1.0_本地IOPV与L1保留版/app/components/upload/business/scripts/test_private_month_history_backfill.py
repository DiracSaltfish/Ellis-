#!/usr/bin/env python3
"""Regression tests for private one-minute historical replay selection."""

from __future__ import annotations

import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_month_history_backfill as backfill


class FakeIB:
    def __init__(self) -> None:
        self.requests: list[tuple[object, object, str, str, str]] = []

    def qualifyContracts(self, contract: object) -> list[object]:  # noqa: N802 - mirrors ib_insync
        return [contract]

    def reqHistoricalData(self, contract: object, end: object, duration: str, bar_size: str, what: str, *_: object) -> list[object]:  # noqa: N802 - mirrors ib_insync
        self.requests.append((contract, end, duration, bar_size, what))
        return [type("Bar", (), {"date": datetime(end.year, end.month, end.day, 10, 0, tzinfo=backfill.xop.SHANGHAI), "close": 1.0})()]


class EmptyIB(FakeIB):
    def reqHistoricalData(self, contract: object, end: object, duration: str, bar_size: str, what: str, *_: object) -> list[object]:  # noqa: N802 - mirrors ib_insync
        self.requests.append((contract, end, duration, bar_size, what))
        return []


class PrivateMonthHistoryBackfillTests(unittest.TestCase):
    def test_public_days_without_start_uses_earliest_public_date(self) -> None:
        with mock.patch.object(backfill, "source_json", return_value={"dates": ["20260710", "20260605", "20260608"]}):
            days = backfill.public_days(
                "https://example.test",
                backfill.SZ159605,
                1,
                date(2026, 6, 1),
                date(2026, 7, 10),
            )
        self.assertEqual(days, [date(2026, 6, 5), date(2026, 6, 8), date(2026, 7, 10)])

    def test_fetch_bar_series_requests_one_minute_bid_ask_windows(self) -> None:
        ib = FakeIB()
        values = backfill.fetch_bar_series(
            ib,
            object(),
            end=datetime(2026, 7, 10, 16, 0, tzinfo=backfill.xop.SHANGHAI),
            what="BID",
            use_rth=False,
            start=date(2026, 6, 5),
            request_delay=0,
        )
        self.assertEqual([(duration, bar_size, what) for _, _, duration, bar_size, what in ib.requests], [("1 M", "1 min", "BID")])
        self.assertEqual(values, {(date(2026, 7, 10), "10:00"): 1.0})

    def test_fetch_bar_series_returns_empty_when_ib_has_no_bars(self) -> None:
        ib = EmptyIB()
        values = backfill.fetch_bar_series(
            ib,
            object(),
            end=datetime(2026, 7, 10, 16, 0, tzinfo=backfill.xop.SHANGHAI),
            what="ASK",
            use_rth=True,
            start=date(2026, 6, 5),
            request_delay=0,
        )
        self.assertEqual(values, {})
        self.assertEqual([duration for _, _, duration, _, _ in ib.requests], ["1 M"])


if __name__ == "__main__":
    unittest.main()
