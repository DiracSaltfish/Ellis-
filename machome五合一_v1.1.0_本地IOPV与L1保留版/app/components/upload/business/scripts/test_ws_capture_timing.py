#!/usr/bin/env python3
import csv
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))

import sina_ws_uploader as ws  # noqa: E402


class CaptureTimingTests(unittest.TestCase):
    def test_market_close_capture_waits_for_delay(self):
        target = datetime(2026, 6, 3, 16, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(ZoneInfo("Asia/Shanghai"))
        self.assertFalse(ws.market_close_capture_ready(target + timedelta(seconds=89), target, 90))
        self.assertTrue(ws.market_close_capture_ready(target + timedelta(seconds=90), target, 90))

    def test_anchor_probe_window_uses_pre_and_post_buffer(self):
        target = datetime(2026, 6, 3, 20, 0, tzinfo=timezone.utc)
        self.assertFalse(ws.probe_window_contains(target - timedelta(seconds=121), target, 120, 180))
        self.assertTrue(ws.probe_window_contains(target - timedelta(seconds=120), target, 120, 180))
        self.assertTrue(ws.probe_window_contains(target + timedelta(seconds=180), target, 120, 180))
        self.assertFalse(ws.probe_window_contains(target + timedelta(seconds=181), target, 120, 180))

    def test_find_stored_anchor_price_prefers_observed_at_nearest_target(self):
        request = {
            "fund_symbol": "SH501018",
            "anchor_date": "2026-06-02",
            "anchor_key": "us_close",
            "reference_symbol": "HF_CL",
            "target_at": "2026-06-02T20:00:00Z",
            "target_timezone": "America/New_York",
            "weight": 1.0,
        }
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "20260603")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "valuation_anchor_prices.csv")
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=ws.ANCHOR_PRICE_FIELDS)
                writer.writeheader()
                writer.writerow(
                    {
                        "stored_at": "2026-06-03T04:01:30+08:00",
                        "fund_symbol": "SH501018",
                        "anchor_date": "2026-06-02",
                        "anchor_key": "us_close",
                        "reference_symbol": "HF_CL",
                        "target_at": "2026-06-02T20:00:00Z",
                        "target_timezone": "America/New_York",
                        "target_beijing_time": "2026-06-03T04:00:00+08:00",
                        "weight": 1.0,
                        "price": 61.25,
                        "observed_at": "2026-06-03T04:01:30+08:00",
                        "source": "sample_late",
                        "capture_status": "captured_current_window",
                    }
                )
                writer.writerow(
                    {
                        "stored_at": "2026-06-03T04:00:05+08:00",
                        "fund_symbol": "SH501018",
                        "anchor_date": "2026-06-02",
                        "anchor_key": "us_close",
                        "reference_symbol": "HF_CL",
                        "target_at": "2026-06-02T20:00:00Z",
                        "target_timezone": "America/New_York",
                        "target_beijing_time": "2026-06-03T04:00:00+08:00",
                        "weight": 1.0,
                        "price": 61.05,
                        "observed_at": "2026-06-03T04:00:05+08:00",
                        "source": "sample_near",
                        "capture_status": "captured_current_window",
                    }
                )
            row = ws.find_stored_anchor_price(root, request)
            self.assertIsNotNone(row)
            self.assertEqual(row["source"], "sample_near")
            self.assertAlmostEqual(row["price"], 61.05)

    def test_find_stored_anchor_price_falls_back_to_probe_quotes(self):
        request = {
            "fund_symbol": "SZ165513",
            "anchor_date": "2026-06-02",
            "anchor_key": "us_close",
            "reference_symbol": "HF_GC",
            "target_at": "2026-06-02T20:00:00Z",
            "target_timezone": "America/New_York",
            "weight": 1.0,
        }
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "20260603")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "quotes.csv")
            fields = [
                "stored_at",
                "reason",
                "symbol",
                "name",
                "price",
                "prev_close",
                "open",
                "high",
                "low",
                "volume",
                "amount",
                "change_pct",
                "quote_date",
                "quote_time",
                "quote_timezone",
                "source",
                "source_symbol",
                "quote_session",
            ]
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(
                    {
                        "stored_at": "2026-06-03T03:59:58+08:00",
                        "reason": ws.anchor_probe_reason(request),
                        "symbol": "HF_GC",
                        "name": "GC",
                        "price": 3379.1,
                        "prev_close": 3378.4,
                        "quote_date": "2026-06-03",
                        "quote_time": "03:59:58",
                        "quote_timezone": "Asia/Shanghai",
                        "source": "sina_hf",
                        "source_symbol": "hf_GC",
                        "quote_session": "global_future",
                    }
                )
            row = ws.find_stored_anchor_price(root, request)
            self.assertIsNotNone(row)
            self.assertEqual(row["capture_status"], "stored_probe_window")
            self.assertAlmostEqual(row["price"], 3379.1)

    def test_sina_us_anchor_uses_market_event_time_not_store_time(self):
        request = {
            "fund_symbol": "SZ162411",
            "anchor_date": "2026-08-07",
            "anchor_key": "us_close",
            "reference_symbol": "XOP",
            "target_at": "2026-08-07T20:00:00Z",
            "target_timezone": "America/New_York",
            "weight": 1.0,
        }
        fields = [
            "stored_at",
            "reason",
            "symbol",
            "name",
            "price",
            "prev_close",
            "open",
            "high",
            "low",
            "volume",
            "amount",
            "change_pct",
            "quote_date",
            "quote_time",
            "quote_timezone",
            "source",
            "source_symbol",
            "quote_session",
        ]
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "20260808")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "quotes.csv")
            common = {
                "reason": ws.anchor_probe_reason(request),
                "symbol": "XOP",
                "name": "XOP",
                "prev_close": 167.05,
                "quote_date": "2026-08-08",
                # Legacy rows mislabeled this Shanghai wall clock as New York.
                "quote_timezone": "America/New_York",
                "source": "sina_us",
                "source_symbol": "gb_xop",
                "quote_session": "regular",
            }
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(
                    {
                        **common,
                        "stored_at": "2026-08-08T03:58:02+08:00",
                        "price": 166.30,
                        "quote_time": "03:57:56",
                    }
                )
                writer.writerow(
                    {
                        **common,
                        # Deliberately far away: selector must not use stored_at.
                        "stored_at": "2026-08-10T12:00:00+08:00",
                        "price": 166.41,
                        "quote_time": "04:00:02",
                    }
                )
                writer.writerow(
                    {
                        **common,
                        # A missing market timestamp cannot be rescued by an
                        # exact-looking storage timestamp.
                        "stored_at": "2026-08-08T04:00:00+08:00",
                        "price": 999.0,
                        "quote_time": "",
                    }
                )

            prices, warnings, missing = ws.resolve_valuation_anchor_price_requests(root, [request])
            self.assertEqual(warnings, [])
            self.assertEqual(missing, [])
            self.assertEqual(len(prices), 1)
            self.assertAlmostEqual(prices[0]["price"], 166.41)
            self.assertEqual(prices[0]["observed_at"], "2026-08-07T20:00:02+00:00")

    def test_stored_anchor_does_not_promote_store_time_to_observed_time(self):
        request = {
            "fund_symbol": "SZ162411",
            "anchor_date": "2026-08-07",
            "anchor_key": "us_close",
            "reference_symbol": "XOP",
            "target_at": "2026-08-07T20:00:00Z",
            "target_timezone": "America/New_York",
            "weight": 1.0,
        }
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "20260808")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "valuation_anchor_prices.csv")
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=ws.ANCHOR_PRICE_FIELDS)
                writer.writeheader()
                writer.writerow(
                    {
                        "stored_at": "2026-08-07T20:00:00Z",
                        **request,
                        "target_beijing_time": "2026-08-08T04:00:00+08:00",
                        "price": 166.41,
                        "observed_at": "",
                        "source": "legacy_anchor",
                        "capture_status": "stored",
                    }
                )

            row = ws.find_stored_anchor_store_price(root, request)
            self.assertIsNotNone(row)
            self.assertEqual(row["observed_at"], "")

    def test_missing_anchor_probe_is_finalized_once(self):
        class Args:
            anchor_probe_pre_seconds = 0
            anchor_capture_delay_seconds = 0
            anchor_probe_post_seconds = 0
            timeout = 1
            source = "test-uploader"

        target_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        request = {
            "fund_symbol": "SZ165513",
            "anchor_date": target_at.date().isoformat(),
            "anchor_key": "us_close",
            "reference_symbol": "HF_GC",
            "target_at": target_at.isoformat(),
            "target_timezone": "America/New_York",
            "weight": 1.0,
        }
        key = ws.anchor_request_key(request)
        sent = []
        original_send_json = ws.send_json
        try:
            ws.send_json = lambda _conn, payload: sent.append(payload)
            with tempfile.TemporaryDirectory() as root:
                args = Args()
                args.store_root = root
                pending = {key: request}
                captured = set()
                ws.capture_due_valuation_anchors(object(), args, pending, captured)
        finally:
            ws.send_json = original_send_json

        self.assertEqual(pending, {})
        self.assertIn(key, captured)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["type"], "valuation_anchor_prices")
        self.assertEqual(sent[0]["valuation_anchor_prices"], [])
        self.assertEqual(len(sent[0]["warnings"]), 1)
        self.assertIn("probe not found", sent[0]["warnings"][0])

    def test_find_stored_daily_price_prefers_source_quote_time(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "20260602")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "quotes.csv")
            fields = [
                "stored_at",
                "reason",
                "symbol",
                "name",
                "price",
                "prev_close",
                "open",
                "high",
                "low",
                "volume",
                "amount",
                "change_pct",
                "quote_date",
                "quote_time",
                "quote_timezone",
                "source",
                "source_symbol",
                "quote_session",
            ]
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(
                    {
                        "stored_at": "2026-06-02T16:20:00+08:00",
                        "reason": "close:us:2026-06-02",
                        "symbol": "QQQ",
                        "name": "QQQ",
                        "price": 500,
                        "prev_close": 499,
                        "quote_date": "2026-06-02",
                        "quote_time": "15:58:00",
                        "quote_timezone": "America/New_York",
                        "source": "sina_us",
                        "source_symbol": "gb_qqq",
                        "quote_session": "regular",
                    }
                )
                writer.writerow(
                    {
                        "stored_at": "2026-06-02T16:19:59+08:00",
                        "reason": "close:us:2026-06-02",
                        "symbol": "QQQ",
                        "name": "QQQ",
                        "price": 501,
                        "prev_close": 499,
                        "quote_date": "2026-06-02",
                        "quote_time": "16:20:00",
                        "quote_timezone": "America/New_York",
                        "source": "sina_us",
                        "source_symbol": "gb_qqq",
                        "quote_session": "regular",
                    }
                )
            row = ws.find_stored_daily_price(root, "QQQ", "2026-06-02", "us")
            self.assertIsNotNone(row)
            self.assertAlmostEqual(row["close"], 501)


class BackfillReconnectTests(unittest.TestCase):
    def test_backfill_sync_reuses_offset_across_calls(self):
        observed_at = datetime(2026, 6, 18, 23, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        rows = [
            {
                "minute_bucket": "2026-06-18T09:31:00+08:00",
                "minute_label": "09:31",
                "trading_day": "2026-06-18",
                "symbol": "SH501312",
                "market": "cn",
                "price": "1.001",
                "open": "1.0",
                "high": "1.002",
                "low": "0.999",
                "volume": "1000",
                "amount": "1000",
            },
            {
                "minute_bucket": "2026-06-18T09:32:00+08:00",
                "minute_label": "09:32",
                "trading_day": "2026-06-18",
                "symbol": "SH501312",
                "market": "cn",
                "price": "1.003",
                "open": "1.001",
                "high": "1.004",
                "low": "1.0",
                "volume": "1200",
                "amount": "1203.6",
            },
        ]
        uploaded: list[list[dict]] = []
        original_upload = ws.upload_intraday_minute_backfill
        try:
            ws.upload_intraday_minute_backfill = lambda _args, payload, force=False: uploaded.append(list(payload)) or len(payload)
            with tempfile.TemporaryDirectory() as root:
                day_key = observed_at.strftime("%Y%m%d")
                folder = os.path.join(root, day_key)
                os.makedirs(folder, exist_ok=True)
                path = os.path.join(folder, "minute_quotes.csv")
                with open(path, "w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=ws.MINUTE_QUOTE_FIELDS)
                    writer.writeheader()
                    writer.writerows(rows)

                sync = ws.IntradayMinuteBackfillSync(root)
                accepted = sync.sync(object(), observed_at, force=True)
                self.assertEqual(accepted, 2)
                self.assertEqual(len(uploaded), 1)
                self.assertEqual(len(uploaded[0]), 2)

                accepted = sync.sync(object(), observed_at, force=True)
                self.assertEqual(accepted, 0)
                self.assertEqual(len(uploaded), 1)

                with open(path, "a", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=ws.MINUTE_QUOTE_FIELDS)
                    writer.writerow(
                        {
                            "minute_bucket": "2026-06-18T09:33:00+08:00",
                            "minute_label": "09:33",
                            "trading_day": "2026-06-18",
                            "symbol": "SH501312",
                            "market": "cn",
                            "price": "1.005",
                            "open": "1.003",
                            "high": "1.006",
                            "low": "1.002",
                            "volume": "900",
                            "amount": "904.5",
                        }
                    )

                accepted = sync.sync(object(), observed_at, force=True)
                self.assertEqual(accepted, 1)
                self.assertEqual(len(uploaded), 2)
                self.assertEqual(len(uploaded[1]), 1)
                self.assertEqual(uploaded[1][0]["minute"], "2026-06-18 09:33")
        finally:
            ws.upload_intraday_minute_backfill = original_upload

    def test_runtime_state_throttles_holdings_upload_across_reconnects(self):
        state = ws.WSRuntimeState()
        self.assertTrue(state.holdings_upload_due(100.0, 1800.0))
        state.note_holdings_upload(100.0, 1800.0)
        self.assertFalse(state.holdings_upload_due(1899.9, 1800.0))
        self.assertTrue(state.holdings_upload_due(1900.0, 1800.0))
        state.reset_holdings_upload()
        self.assertTrue(state.holdings_upload_due(101.0, 1800.0))


if __name__ == "__main__":
    unittest.main()
