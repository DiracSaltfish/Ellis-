#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import ib_us_uploader_support as support


BEIJING = ZoneInfo("Asia/Shanghai")


def build_args(**overrides):
    values = dict(
        enabled=True,
        host="127.0.0.1",
        port=7496,
        client_id=22961,
        timeout=2.0,
        server="https://example.invalid",
        token="test-token",
        source="test",
        store_root="",
        origin_ip="",
        origin_tls_insecure=False,
        origin_ca_file="",
        holding_funds=(),
        holding_refresh_interval=600.0,
        extra_symbols=("XOP", "INDA"),
        live_mode="smart",
        active_start_minute=9 * 60,
        active_end_minute=15 * 60 + 5,
        connect_retry_seconds=30.0,
        connect_timeout=2.0,
        warmup_seconds=0.0,
        stale_after_seconds=90.0,
        stale_reconnect_streak=2,
    )
    values.update(overrides)
    return support.IBBridgeArgs(**values)


class FakeSharedStream:
    instances = []

    def __init__(self, subscriptions, timeout):
        self.subscriptions = tuple(subscriptions)
        self.timeout = timeout
        self.connected = False
        self.closed = False
        self.__class__.instances.append(self)

    def connect(self):
        self.connected = True

    def is_connected(self):
        return self.connected and not self.closed

    def poll(self, wait_seconds=0.0):
        observed = datetime(2026, 9, 4, 5, 50, tzinfo=timezone.utc)
        return {
            item.subscription_id: SimpleNamespace(
                last=192.20 if item.symbol == "XOP" else 49.76,
                bid=192.10 if item.symbol == "XOP" else 49.70,
                ask=192.30 if item.symbol == "XOP" else 49.80,
                close=192.33 if item.symbol == "XOP" else 49.92,
                exchange_timestamp=None,
                observed_at=observed,
                market_data_type="Live",
                sequence=41,
                age_ms=12,
            )
            for item in self.subscriptions
        }

    def close(self):
        self.closed = True
        self.connected = False


class FakeDirectIB:
    def __init__(self):
        self.disconnect_count = 0

    def isConnected(self):
        return True

    def disconnect(self):
        self.disconnect_count += 1


class SharedLiveBridgeTests(unittest.TestCase):
    def setUp(self):
        FakeSharedStream.instances.clear()

    def test_smart_live_quotes_use_one_shared_stream_and_no_direct_ib(self):
        bridge = support.SharedIBUSHoldingsBridge(build_args())
        bridge.refresh_target_symbols = lambda *args, **kwargs: {"XOP", "INDA"}
        observed = datetime(2026, 9, 4, 13, 50, tzinfo=BEIJING)

        with patch.object(support, "SharedMultiQuoteStream", FakeSharedStream):
            first = bridge.live_quotes(["XOP", "INDA"], observed)
            second = bridge.live_quotes(["XOP", "INDA"], observed)

        self.assertIsNone(bridge.ib)
        self.assertEqual(len(FakeSharedStream.instances), 1)
        subscriptions = FakeSharedStream.instances[0].subscriptions
        self.assertEqual({item.subscription_id for item in subscriptions}, {"XOP.SMART", "INDA.SMART"})
        by_symbol = {item.symbol: item for item in subscriptions}
        self.assertEqual(by_symbol["XOP"].primary_exchange, "ARCA")
        self.assertEqual(by_symbol["XOP"].generic_ticks, "236")
        self.assertEqual(by_symbol["INDA"].primary_exchange, "ARCA")
        self.assertEqual(by_symbol["INDA"].generic_ticks, "")
        self.assertEqual(set(first), {"XOP", "INDA"})
        self.assertEqual(second["XOP"]["source"], "ibkr_us_shared_cpp")
        self.assertEqual(second["XOP"]["market_data_type"], "Live")
        self.assertEqual(second["XOP"]["bridge_sequence"], 41)

    def test_bridge_connect_failure_does_not_fallback_to_python_tws(self):
        class FailingStream(FakeSharedStream):
            def connect(self):
                raise RuntimeError("bridge unavailable")

        bridge = support.SharedIBUSHoldingsBridge(build_args())
        bridge.refresh_target_symbols = lambda *args, **kwargs: {"XOP"}
        observed = datetime(2026, 9, 4, 13, 50, tzinfo=BEIJING)

        with patch.object(support, "SharedMultiQuoteStream", FailingStream):
            with self.assertRaisesRegex(RuntimeError, "bridge unavailable"):
                bridge.live_quotes(["XOP"], observed)

        self.assertIsNone(bridge.ib)
        self.assertIsNone(bridge.shared_stream)

    def test_historical_operation_always_closes_direct_session_but_keeps_shared_lease(self):
        bridge = support.SharedIBUSHoldingsBridge(build_args())
        direct = FakeDirectIB()
        shared = FakeSharedStream((), 1.0)
        shared.connect()
        bridge.ib = direct
        bridge.shared_stream = shared
        bridge.shared_target = ("XOP",)

        with patch.object(
            support.IBUSHoldingsBridge,
            "resolve_missing_daily_prices",
            return_value=([], [], []),
        ):
            self.assertEqual(bridge.resolve_missing_daily_prices([{"symbol": "XOP"}]), ([], [], []))

        self.assertEqual(direct.disconnect_count, 1)
        self.assertIsNone(bridge.ib)
        self.assertTrue(shared.is_connected())

    def test_historical_exception_still_closes_direct_session(self):
        bridge = support.SharedIBUSHoldingsBridge(build_args())
        direct = FakeDirectIB()
        bridge.ib = direct

        with patch.object(
            support.IBUSHoldingsBridge,
            "catchup_reference_minutes",
            side_effect=RuntimeError("historical failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "historical failure"):
                bridge.catchup_reference_minutes(["XOP"])

        self.assertEqual(direct.disconnect_count, 1)
        self.assertIsNone(bridge.ib)

    def test_factory_returns_shared_adapter(self):
        args = SimpleNamespace(
            timeout=2.0,
            server="https://example.invalid",
            token="test-token",
            source="test",
            store_root="",
            origin_ip="",
            origin_tls_insecure=False,
            origin_ca_file="",
        )
        environment = {
            "NNN_ENABLE_IB_US_HOLDINGS": "1",
            "NNN_IB_HOLDING_FUNDS": "",
            "NNN_IB_EXTRA_SYMBOLS": "XOP",
            "NNN_IB_US_LIVE_MODE": "smart",
        }
        with patch.dict("os.environ", environment, clear=False):
            bridge = support.bridge_from_args(args)

        self.assertIsInstance(bridge, support.SharedIBUSHoldingsBridge)


if __name__ == "__main__":
    unittest.main()
