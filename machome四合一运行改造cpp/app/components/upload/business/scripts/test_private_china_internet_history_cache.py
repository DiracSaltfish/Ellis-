#!/usr/bin/env python3
"""Regression tests for reusing the local IB BID/ASK cache in history replay."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_china_internet_history_backfill as history  # noqa: E402
import private_china_internet_valuation_uploader as valuation  # noqa: E402


def write_series(root: Path, market: str, symbol: str, side: str, values: dict[tuple[date, str], float]) -> None:
    path = root / "series" / f"{market}_{symbol}_{side}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["trading_day", "minute", "market", "symbol", "currency", "side", "price"])
        writer.writeheader()
        for (trading_day, minute), price in sorted(values.items()):
            writer.writerow({"trading_day": trading_day.isoformat(), "minute": minute, "market": market, "symbol": symbol, "currency": "HKD" if market == "HK" else "USD", "side": side, "price": price})


class HistoryCacheTest(unittest.TestCase):
    def test_reuses_cache_and_does_not_request_known_market_holiday(self) -> None:
        june_22, july_1, july_10 = date(2026, 6, 22), date(2026, 7, 1), date(2026, 7, 10)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps({"status": "completed", "public_minute_history_days": [item.isoformat() for item in (june_22, july_1, july_10)]}), encoding="utf-8")
            for side in ("BID", "ASK"):
                write_series(root, "HK", "0700", side, {(june_22, "09:30"): 100.0, (july_10, "09:30"): 101.0})
            cached, closed_days = history.load_ib_bid_ask_cache(root)
            self.assertEqual(closed_days, {"HK": {july_1}})
            calls: list[tuple[str, tuple[date, ...]]] = []

            def fake_fetch(_ib, _contract, *, what, use_rth, days, request_delay):
                del use_rth, request_delay
                calls.append((what, tuple(days)))
                return {(item, "09:30"): 99.0 for item in days}

            original = history.fetch_bar_series
            history.fetch_bar_series = fake_fetch
            try:
                args = Namespace(ib_request_delay=0)
                component = valuation.Component("0700", "Tencent", "HK", "HKD", 1.0)
                result = history.collect_overseas_series(args, None, {component.key: component}, (june_22, july_1, july_10), cached, closed_days)
            finally:
                history.fetch_bar_series = original
            self.assertEqual(calls, [])
            self.assertEqual(set(day for day, _minute in result[("HK", "0700", "BID")]), {june_22, july_10})
