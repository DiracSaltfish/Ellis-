#!/usr/bin/env python3
import csv
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))

import intraday_minute_store as store  # noqa: E402


BEIJING = ZoneInfo("Asia/Shanghai")


def quote(symbol: str, price: float, quote_time: str) -> dict:
    return {
        "symbol": symbol,
        "name": symbol,
        "price": price,
        "prev_close": price - 1,
        "open": price - 0.5,
        "high": price + 0.5,
        "low": price - 0.75,
        "volume": 1000,
        "amount": 2000,
        "change_pct": 0.1,
        "quote_date": "2026-06-04",
        "quote_time": quote_time,
        "quote_timezone": "Asia/Shanghai",
        "quote_session": "cn_regular",
        "source": "sample",
        "source_symbol": symbol,
    }


class IntradayMinuteArchiveTests(unittest.TestCase):
    def test_records_latest_quote_once_per_minute(self):
        with tempfile.TemporaryDirectory() as root:
            archive = store.IntradayMinuteArchive(root)
            t0 = datetime(2026, 6, 4, 9, 30, 5, tzinfo=BEIJING)
            archive.record([quote("SH513100", 1.01, "09:30:04")], t0)
            archive.record([quote("SH513100", 1.02, "09:30:27")], t0 + timedelta(seconds=22))
            archive.flush_due(t0 + timedelta(minutes=1))

            path = os.path.join(root, "20260604", "minute_quotes.csv")
            with open(path, newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["minute_label"], "09:30")
            self.assertEqual(rows[0]["symbol"], "SH513100")
            self.assertEqual(rows[0]["market"], "cn")
            self.assertEqual(rows[0]["capture_count"], "2")
            self.assertEqual(rows[0]["price"], "1.02")

    def test_records_domestic_and_external_symbols_together(self):
        with tempfile.TemporaryDirectory() as root:
            archive = store.IntradayMinuteArchive(root)
            t0 = datetime(2026, 6, 4, 10, 5, 0, tzinfo=BEIJING)
            archive.record(
                [
                    quote("SH513100", 1.01, "10:04:59"),
                    quote("HF_NQ", 21234.0, "10:04:58"),
                ],
                t0,
            )
            archive.flush_due(t0 + timedelta(minutes=1))

            path = os.path.join(root, "20260604", "minute_quotes.csv")
            with open(path, newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            rows_by_symbol = {row["symbol"]: row for row in rows}
            self.assertEqual(rows_by_symbol["SH513100"]["market"], "cn")
            self.assertEqual(rows_by_symbol["HF_NQ"]["market"], "us_commodity_futures")

    def test_skips_outside_cn_intraday_session(self):
        with tempfile.TemporaryDirectory() as root:
            archive = store.IntradayMinuteArchive(root)
            t0 = datetime(2026, 6, 4, 8, 59, 0, tzinfo=BEIJING)
            archive.record([quote("SH513100", 1.01, "08:59:00")], t0)
            archive.close()
            self.assertFalse(os.path.exists(os.path.join(root, "20260604", "minute_quotes.csv")))


if __name__ == "__main__":
    unittest.main()
