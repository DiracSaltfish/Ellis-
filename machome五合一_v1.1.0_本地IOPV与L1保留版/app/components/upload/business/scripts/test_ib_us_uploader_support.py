#!/usr/bin/env python3
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import ib_us_uploader_support as support


BEIJING = ZoneInfo("Asia/Shanghai")


class FakeBar:
    def __init__(self, when: datetime, close: float = 1.0):
        self.date = when
        self.close = close
        self.open = close
        self.high = close
        self.low = close
        self.volume = 0


class FakeIB:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.cancelled = []
        self.disconnected = 0

    def cancelHistoricalData(self, bars):
        self.cancelled.append(bars)

    def reqHistoricalData(self, *args, **kwargs):
        if not self.responses:
            return []
        return self.responses.pop(0)

    def sleep(self, seconds):
        return None

    def disconnect(self):
        self.disconnected += 1

    def isConnected(self):
        return True


def build_args(**overrides):
    values = dict(
        enabled=True,
        host="127.0.0.1",
        port=7496,
        client_id=22961,
        timeout=8.0,
        server="https://1navs.com",
        token="token",
        source="home-mac",
        store_root="",
        origin_ip="",
        origin_tls_insecure=False,
        origin_ca_file="",
        holding_funds=("SH501312",),
        holding_refresh_interval=600.0,
        extra_symbols=("XOP",),
        live_mode="overnight",
        active_start_minute=9 * 60,
        active_end_minute=15 * 60 + 5,
        connect_retry_seconds=30.0,
        connect_timeout=20.0,
        warmup_seconds=0.0,
        stale_after_seconds=90.0,
        stale_reconnect_streak=2,
    )
    values.update(overrides)
    return support.IBBridgeArgs(**values)


class IBWindowTests(unittest.TestCase):
    def test_parse_ib_anchor_datetime_accepts_rfc3339_z(self):
        parsed = support.parse_ib_anchor_datetime("2026-08-31T06:30:00Z")

        self.assertEqual(parsed, datetime(2026, 8, 31, 6, 30, tzinfo=timezone.utc))

    def test_parse_hhmm_to_minute(self):
        self.assertEqual(support.parse_hhmm_to_minute("09:00", "00:00"), 9 * 60)
        self.assertEqual(support.parse_hhmm_to_minute("15:05", "00:00"), 15 * 60 + 5)
        self.assertEqual(support.parse_hhmm_to_minute("bad", "09:00"), 9 * 60)

    def test_is_ib_active_window_weekday(self):
        start = support.parse_hhmm_to_minute("09:00", "09:00")
        end = support.parse_hhmm_to_minute("15:05", "15:05")
        self.assertFalse(support.is_ib_active_window(datetime(2026, 6, 4, 8, 59, tzinfo=BEIJING), start, end))
        self.assertTrue(support.is_ib_active_window(datetime(2026, 6, 4, 9, 0, tzinfo=BEIJING), start, end))
        self.assertTrue(support.is_ib_active_window(datetime(2026, 6, 4, 15, 5, tzinfo=BEIJING), start, end))
        self.assertFalse(support.is_ib_active_window(datetime(2026, 6, 4, 15, 6, tzinfo=BEIJING), start, end))

    def test_is_ib_active_window_weekend_disabled(self):
        start = support.parse_hhmm_to_minute("09:00", "09:00")
        end = support.parse_hhmm_to_minute("15:05", "15:05")
        self.assertFalse(support.is_ib_active_window(datetime(2026, 6, 6, 10, 0, tzinfo=BEIJING), start, end))

    def test_overnight_probe_order_symbol_overrides(self):
        self.assertEqual(support.overnight_probe_order("ARKG"), ("MIDPOINT", "TRADES"))
        self.assertEqual(support.overnight_probe_order("ARKQ"), ("MIDPOINT", "TRADES"))
        self.assertEqual(support.overnight_probe_order("FINX"), ("MIDPOINT", "TRADES"))
        self.assertEqual(support.overnight_probe_order("QQQ"), ("TRADES", "MIDPOINT"))


class IBBridgeStaleStreamTests(unittest.TestCase):
    def test_ensure_overnight_stream_rebuilds_stale_subscription(self):
        bridge = support.IBUSHoldingsBridge(build_args())
        observed_at = datetime(2026, 6, 12, 14, 0, tzinfo=BEIJING)
        stale_bars = [FakeBar(datetime(2026, 6, 12, 12, 22, tzinfo=BEIJING), close=10.0)]
        fresh_bars = [FakeBar(datetime(2026, 6, 12, 13, 59, tzinfo=BEIJING), close=11.0)]
        fake_ib = FakeIB([fresh_bars])
        bridge.ib = fake_ib
        bridge.last_connect_at = 1.0
        bridge.overnight_streams["XOP"] = (stale_bars, "TRADES")
        bridge.ensure_connected = lambda observed_at=None: True
        bridge.qualify_overnight_contract = lambda symbol, primary_exchange: object()
        bridge.primary_exchange = lambda symbol: ""

        bridge.ensure_overnight_stream("XOP", observed_at)

        self.assertEqual(fake_ib.cancelled, [stale_bars])
        self.assertEqual(bridge.overnight_streams["XOP"], (fresh_bars, "TRADES"))
        self.assertEqual(bridge.overnight_stream_stale_counts.get("XOP"), None)

    def test_overnight_stream_quote_triggers_runtime_reconnect_after_repeated_stale(self):
        bridge = support.IBUSHoldingsBridge(build_args())
        observed_at = datetime(2026, 6, 12, 14, 0, tzinfo=BEIJING)
        stale_bars = [FakeBar(datetime(2026, 6, 12, 12, 22, tzinfo=BEIJING), close=10.0)]
        fake_ib = FakeIB()
        bridge.ib = fake_ib
        bridge.last_connect_at = 0.0
        bridge.last_subscription_change_at = 0.0
        bridge.overnight_streams["XOP"] = (stale_bars, "TRADES")

        first = bridge.overnight_stream_quote("XOP", observed_at)
        self.assertIsNone(first)
        self.assertEqual(bridge.overnight_stream_stale_counts["XOP"], 1)
        self.assertEqual(fake_ib.disconnected, 0)

        bridge.overnight_streams["XOP"] = (stale_bars, "TRADES")
        second = bridge.overnight_stream_quote("XOP", observed_at)
        self.assertIsNone(second)
        self.assertEqual(fake_ib.disconnected, 1)
        self.assertEqual(bridge.overnight_stream_stale_counts, {})


class IBAnchorDeduplicationTests(unittest.TestCase):
    def test_future_anchor_request_does_not_open_ib_connection(self):
        bridge = support.IBUSHoldingsBridge(build_args(extra_symbols=("XOP",)))
        bridge.ensure_connected = lambda observed_at=None: self.fail("future request connected to IB")
        request = {
            "fund_symbol": "SZ162411", "anchor_date": "2026-08-31",
            "anchor_key": "us_close", "reference_symbol": "XOP",
            "target_at": "2026-08-31T20:00:00Z",
            "target_timezone": "America/New_York", "weight": 1,
        }

        with patch.object(
            support,
            "now_beijing",
            return_value=datetime(2026, 8, 31, 9, 36, tzinfo=BEIJING),
        ):
            rows, warnings = bridge.resolve_missing_valuation_anchor_prices([request])

        self.assertEqual(rows, [])
        self.assertEqual(warnings, [])

    def test_duplicate_reference_and_target_is_fetched_once_per_batch(self):
        bridge = support.IBUSHoldingsBridge(build_args(extra_symbols=("XOP",)))
        bridge.ib = FakeIB()
        bridge.ensure_connected = lambda observed_at=None: True
        bridge.refresh_target_symbols = lambda *args, **kwargs: {"XOP"}
        calls: list[tuple[str, datetime]] = []

        def fetch(symbol, request, target_at):
            calls.append((symbol, target_at))
            return {
                "price": 177.41,
                "observed_at": target_at.isoformat(),
                "source": "daily",
                "capture_status": "captured_exact",
            }

        bridge.fetch_us_anchor = fetch
        target = "2026-08-12T20:00:00+00:00"
        requests = [
            {
                "fund_symbol": "SZ162411", "anchor_date": "2026-08-12",
                "anchor_key": "us_close", "reference_symbol": "XOP",
                "target_at": target, "target_timezone": "America/New_York", "weight": 1,
            },
            {
                "fund_symbol": "SZ159518", "anchor_date": "2026-08-12",
                "anchor_key": "us_close", "reference_symbol": "XOP",
                "target_at": target, "target_timezone": "America/New_York", "weight": 0.95,
            },
        ]

        rows, warnings = bridge.resolve_missing_valuation_anchor_prices(requests)

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["fund_symbol"] for row in rows}, {"SZ162411", "SZ159518"})
        self.assertFalse(warnings)


if __name__ == "__main__":
    unittest.main()
