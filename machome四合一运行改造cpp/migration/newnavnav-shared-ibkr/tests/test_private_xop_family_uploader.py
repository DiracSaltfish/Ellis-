#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import private_valuation_uploader as common  # noqa: E402
import private_xop_family_uploader as uploader  # noqa: E402


class XOPFamilyUploaderTests(unittest.TestCase):
    def quote(self, bid: float, ask: float, at: datetime) -> common.IBQuote:
        return common.IBQuote(bid, ask, (bid + ask) / 2, "Live", at, at)

    def test_last_sanitizer_never_scales_corrupt_price(self) -> None:
        self.assertEqual(uploader.sanitize_last(177.41, 177.36, 177.53), 177.41)
        self.assertIsNone(uploader.sanitize_last(17.741, 177.36, 177.53))
        self.assertIsNone(uploader.sanitize_last(float("nan"), 177.36, 177.53))

    def test_route_audit_labels_remain_explicit(self) -> None:
        at = datetime(2026, 8, 13, 10, 0, tzinfo=common.SHANGHAI)
        smart = uploader.audited_quote_payload(self.quote(177.36, 177.53, at), uploader.SMART)
        overnight = uploader.audited_quote_payload(
            self.quote(177.33, 177.55, at), uploader.OVERNIGHT
        )
        self.assertEqual(smart["quote_session"], "us_smart_live")
        self.assertIn("SMART/ARCA", smart["contract"])
        self.assertEqual(overnight["quote_session"], "us_overnight_live")
        self.assertIn("OVERNIGHT/ARCA", overnight["contract"])

    def test_hub_opens_one_client_and_two_subscriptions(self) -> None:
        captured: list[dict] = []

        class FakeSubscription:
            @staticmethod
            def create(**kwargs):
                captured.append(dict(kwargs))
                return types.SimpleNamespace(subscription_id=kwargs["subscription_id"])

        class FakeMulti:
            def __init__(self, subscriptions, timeout):
                self.timeout = timeout
            def connect(self):
                return None
            def is_connected(self):
                return True
            def close(self):
                return None

        module = types.SimpleNamespace(
            ContractSubscription=FakeSubscription, SharedMultiQuoteStream=FakeMulti,
        )
        with mock.patch.dict(sys.modules, {"machome_ibkr_bridge_client": module}):
            hub = uploader.XOPMarketHub("192.168.1.111", 7496, 159518, 40.0, uploader.DUAL_ROUTE)
            hub.connect()
            self.assertEqual(len(captured), 2)
            self.assertEqual(set(hub.subscription_ids), {uploader.SMART, uploader.OVERNIGHT})
            smart = next(item for item in captured if item["subscription_id"] == "XOP.SMART")
            self.assertNotIn("con_id", smart)
            self.assertEqual(smart["primary_exchange"], "ARCA")
            self.assertEqual(smart["generic_ticks"], "236")
            self.assertEqual(hub.shared.timeout, 40.0)
            hub.close()

    def test_production_smart_mode_has_one_subscription_for_all_funds(self) -> None:
        args = types.SimpleNamespace(mode=uploader.SMART)
        collector = object.__new__(uploader.XOPFamilyCollector)
        collector.args = args
        self.assertEqual(collector.route_for(common.SYMBOL), uploader.SMART)
        self.assertEqual(collector.route_for("SH513350"), uploader.SMART)
        self.assertEqual(collector.route_for("SZ162411"), uploader.SMART)
        hub = uploader.XOPMarketHub("192.168.1.111", 7496, 159519, 40.0, uploader.SMART)
        self.assertEqual(hub.required_routes(), (uploader.SMART,))

    def test_stale_market_data_packet_forces_reconnect(self) -> None:
        now = datetime(2026, 8, 31, 10, 4, tzinfo=common.SHANGHAI)

        hub = uploader.XOPMarketHub("192.168.1.111", 7496, 159519, 40.0, uploader.SMART)
        hub.shared = types.SimpleNamespace(
            is_connected=lambda: True,
            poll=lambda _seconds: {"XOP.SMART": types.SimpleNamespace(
                bid=187.15,
                ask=188.71,
                last=187.92,
                market_data_type="Live",
                observed_at=now - timedelta(seconds=uploader.MAX_STREAM_PACKET_AGE_SECONDS + 1),
            )},
        )
        hub.subscription_ids = {uploader.SMART: "XOP.SMART"}

        with mock.patch.object(uploader, "datetime") as patched_datetime:
            patched_datetime.now.return_value = now
            with self.assertRaisesRegex(
                common.SourceUnavailableError,
                r"smart market-data stream is stale.*reconnect required",
            ):
                hub.poll(0)

    def test_recent_market_data_packet_is_accepted(self) -> None:
        now = datetime(2026, 8, 31, 10, 4, tzinfo=common.SHANGHAI)

        hub = uploader.XOPMarketHub("192.168.1.111", 7496, 159519, 40.0, uploader.SMART)
        hub.shared = types.SimpleNamespace(
            is_connected=lambda: True,
            poll=lambda _seconds: {"XOP.SMART": types.SimpleNamespace(
                bid=187.92,
                ask=188.54,
                last=188.15,
                market_data_type="Live",
                observed_at=now - timedelta(seconds=uploader.MAX_STREAM_PACKET_AGE_SECONDS),
            )},
        )
        hub.subscription_ids = {uploader.SMART: "XOP.SMART"}

        with mock.patch.object(uploader, "datetime") as patched_datetime:
            patched_datetime.now.return_value = now
            quotes = hub.poll(0)

        self.assertEqual(quotes[uploader.SMART].bid, 187.92)
        self.assertEqual(quotes[uploader.SMART].ask, 188.54)

    def test_shadow_gate_requires_five_days_and_all_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            recorder = uploader.ShadowRecorder(temp_dir)
            start = datetime(2026, 8, 10, 10, 0, tzinfo=common.SHANGHAI)
            for day in range(5):
                at = start + timedelta(days=day)
                quote = self.quote(177.36, 177.53, at)
                recorder.record(quote, quote)
            report = recorder.report()
            self.assertEqual(report["days"], 5)
            self.assertEqual(report["exact_equality_rate"], 1.0)
            self.assertTrue(report["eligible_for_single_route"])

            path = Path(temp_dir) / "shadow" / "2026-08-14.jsonl"
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            row["ask_delta"] = 0.02
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            self.assertFalse(recorder.report()["eligible_for_single_route"])

    def test_source_worker_does_not_block_caller_on_slow_refresh(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class SlowCollector:
            args = types.SimpleNamespace(timeout=0.01)

            def refresh(self, _now) -> None:
                entered.set()
                release.wait(1.0)

        worker = uploader.SourceRefreshWorker(SlowCollector())
        worker.start()
        self.assertTrue(entered.wait(0.2))
        self.assertTrue(worker.thread.is_alive())
        release.set()
        worker.close()
        self.assertFalse(worker.thread.is_alive())

    def test_source_worker_installs_asyncio_loop_for_ib_insync(self) -> None:
        observed_loop: list[asyncio.AbstractEventLoop] = []
        entered = threading.Event()

        class LoopCollector:
            args = types.SimpleNamespace(timeout=0.01)

            def refresh(self, _now) -> None:
                observed_loop.append(asyncio.get_event_loop())
                entered.set()

        worker = uploader.SourceRefreshWorker(LoopCollector())
        worker.start()
        self.assertTrue(entered.wait(0.2))
        worker.close()
        self.assertGreaterEqual(len(observed_loop), 1)
        self.assertEqual(len({id(loop) for loop in observed_loop}), 1)
        self.assertFalse(observed_loop[0].is_running())
        self.assertTrue(observed_loop[0].is_closed())

    def test_cfets_spot_populates_shared_fx_and_safe_fills_lof_current(self) -> None:
        now = datetime(2026, 8, 19, 9, 30, tzinfo=common.SHANGHAI)
        spot = common.CFETSSpotQuote("USD/CNY", 6.79, now.date(), "09:30", now)
        parity = types.SimpleNamespace(rate=6.7904, trading_day=now.date(), fetched_at=now)

        class FakeCFETS:
            def fetch(self, pair, day):
                self.request = (pair, day)
                return spot

        with tempfile.TemporaryDirectory() as temp_dir:
            collector = object.__new__(uploader.XOPFamilyCollector)
            collector.args = types.SimpleNamespace(
                force=False, pcf_start_minute=8 * 60 + 30, fx_interval=60.0, timeout=1.0
            )
            collector.cfets = FakeCFETS()
            collector.state_lock = threading.RLock()
            collector.state = uploader.FamilyState()
            collector.day = now.date()
            collector.next_pcf = float("inf")
            collector.next_cfets = 0.0
            collector.next_seed = float("inf")
            collector.next_safe = 0.0
            collector.next_calibration = float("inf")

            with mock.patch.object(
                uploader.safe_common,
                "fetch_safe_central_parity",
                return_value=parity,
            ):
                collector.refresh(now)
            self.assertEqual(collector.cfets.request, ("USD/CNY", now.date()))
            self.assertEqual(collector.state.cfets, spot)
            self.assertEqual(collector.state.current_fx, spot)
            self.assertEqual(collector.state.current_safe, parity)

    def test_current_safe_parity_skipped_before_nine_fifteen(self) -> None:
        now = datetime(2026, 8, 19, 9, 10, tzinfo=common.SHANGHAI)

        collector = object.__new__(uploader.XOPFamilyCollector)
        collector.args = types.SimpleNamespace(
            force=False, pcf_start_minute=8 * 60 + 30, fx_interval=60.0, timeout=1.0
        )
        collector.state_lock = threading.RLock()
        collector.state = uploader.FamilyState()
        collector.day = now.date()
        collector.next_pcf = float("inf")
        collector.next_cfets = float("inf")
        collector.next_seed = float("inf")
        collector.next_safe = 0.0
        collector.next_calibration = float("inf")

        with mock.patch.object(
            uploader.safe_common,
            "fetch_safe_central_parity",
            side_effect=AssertionError("must not fetch before 09:15"),
        ):
            collector.refresh(now)
        self.assertIsNone(collector.state.current_safe)

    def test_complete_build_has_exactly_three_unique_symbols(self) -> None:
        now = datetime(2026, 8, 13, 10, 0, tzinfo=common.SHANGHAI)
        collector = object.__new__(uploader.XOPFamilyCollector)
        collector.args = types.SimpleNamespace(mode=uploader.SMART, source="test")
        collector.state_lock = threading.RLock()
        collector.state = uploader.FamilyState(
            pcf159518=object(),
            pcf513350=object(),
            cfets=object(),
            seed162411=object(),
            base_safe=object(),
            current_fx=object(),
            current_safe=object(),
            calibration513350=object(),
        )
        quotes = {uploader.SMART: self.quote(177.36, 177.53, now)}
        with (
            mock.patch.object(
                uploader.common,
                "build_private_payload",
                return_value={"symbol": common.SYMBOL, "ib": {}},
            ),
            mock.patch.object(
                uploader.fund513350,
                "build_private_payload",
                return_value={"symbol": uploader.fund513350.SYMBOL, "ib": {}},
            ),
            mock.patch.object(
                uploader.lof162411,
                "build_payload",
                return_value={"symbol": uploader.lof162411.SYMBOL, "ib": {}},
            ),
        ):
            inputs, failures = collector.build_inputs(quotes, now)
        symbols = [value["symbol"] for value in inputs]
        self.assertEqual(failures, {})
        self.assertEqual(len(symbols), 3)
        self.assertEqual(len(set(symbols)), 3)
        self.assertEqual(
            set(symbols),
            {common.SYMBOL, uploader.fund513350.SYMBOL, uploader.lof162411.SYMBOL},
        )


if __name__ == "__main__":
    unittest.main()
