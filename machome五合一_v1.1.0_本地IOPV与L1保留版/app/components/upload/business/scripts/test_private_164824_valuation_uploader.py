import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
import sys
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import private_164824_valuation_uploader as uploader


class Private164824BridgeTests(unittest.TestCase):
    def test_ib_handshake_timeout_defaults_to_40_seconds(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            args = uploader.parser().parse_args([])
        self.assertEqual(args.ib_timeout, 40.0)

    def quote(self, symbol: str, bid: float, ask: float, observed_at: datetime) -> uploader.MarketQuote:
        return uploader.MarketQuote(symbol, f"{symbol}Q26", bid, ask, (bid + ask) / 2, observed_at, "Live")

    def test_bridge_cache_uses_new_york_close_window_and_median_reference(self) -> None:
        new_york = ZoneInfo("America/New_York")
        shanghai = ZoneInfo("Asia/Shanghai")
        first = datetime(2026, 7, 9, 15, 49, 0, tzinfo=new_york)
        second = datetime(2026, 7, 9, 15, 51, 0, tzinfo=new_york)
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            uploader.record_bridge_sample(runtime, first, self.quote("INDA", 100, 100.1, first), self.quote("NIFTY", 20_000, 20_002, first))
            uploader.record_bridge_sample(runtime, second, self.quote("INDA", 100.2, 100.3, second), self.quote("NIFTY", 20_020, 20_022, second))
            reference = uploader.latest_bridge_reference(runtime, datetime(2026, 7, 10, 10, 0, tzinfo=shanghai))

        self.assertIsNotNone(reference)
        reference_at, inda, nifty = reference or (None, None, None)
        self.assertEqual(reference_at, second)
        self.assertAlmostEqual(inda.bid, 100.1)
        self.assertAlmostEqual(nifty.ask, 20_012)

    def test_build_payload_adds_optional_nifty_bridge_without_replacing_inda(self) -> None:
        now = datetime(2026, 7, 10, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        parity = SimpleNamespace(rate=7.0, trading_day=date(2026, 7, 8), fetched_at=now)
        current_parity = uploader.common.CFETSSpotQuote(
            "USD/CNY", 7.1, date(2026, 7, 10), "10:00", now,
        )
        anchors = [
            uploader.AnchorObservation(spec.key, spec.label, spec.weight, 100, now.isoformat(), now.isoformat(), "test", "exact_1m")
            for spec in uploader.ANCHORS
        ]
        reference_at = datetime(2026, 7, 9, 15, 50, tzinfo=ZoneInfo("America/New_York"))
        payload = uploader.build_payload(
            uploader.OfficialNAV(1.0, date(2026, 7, 8)), parity, current_parity,
            self.quote("INDA", 101, 102, now), anchors, now,
            self.quote("NIFTY", 20_100, 20_102, now),
            (reference_at, self.quote("INDA", 100, 100.1, reference_at), self.quote("NIFTY", 20_000, 20_002, reference_at)),
        )

        self.assertEqual(payload["ib"]["symbol"], "INDA")
        self.assertEqual(payload["india"]["current_fx"]["source"], "CFETS_SPOT_RATE")
        bridge = payload["india"]["nifty_bridge"]
        self.assertEqual(bridge["nifty"]["symbol"], "NIFTY")
        self.assertEqual(bridge["inda_reference"]["symbol"], "INDA")
        self.assertEqual(bridge["beta"], 1.0)

    def test_bridge_cache_rejects_delayed_reference_quote(self) -> None:
        captured_at = datetime(2026, 7, 9, 15, 50, tzinfo=ZoneInfo("America/New_York"))
        delayed = uploader.MarketQuote("INDA", "INDA", 100, 100.1, 100.05, captured_at, "Delayed")
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(uploader.SourceUnavailableError):
                uploader.record_bridge_sample(Path(raw), captured_at, delayed, self.quote("NIFTY", 20_000, 20_002, captured_at))

    def test_historical_bridge_reference_is_auditable_after_tws_open(self) -> None:
        new_york = ZoneInfo("America/New_York")
        shanghai = ZoneInfo("Asia/Shanghai")
        captured_at = datetime(2026, 8, 4, 15, 50, tzinfo=new_york)
        historical_inda = uploader.MarketQuote(
            "INDA", "INDA", 50, 50.01, None, captured_at,
            uploader.HISTORICAL_BID_ASK_MARKET_DATA_TYPE, uploader.HISTORICAL_BID_ASK_SOURCE,
        )
        historical_nifty = uploader.MarketQuote(
            "NIFTY", "AUG", 20_000, 20_002, None, captured_at,
            uploader.HISTORICAL_BID_ASK_MARKET_DATA_TYPE, uploader.HISTORICAL_BID_ASK_SOURCE,
        )
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            uploader.record_bridge_sample(runtime, captured_at, historical_inda, historical_nifty)
            reference = uploader.latest_bridge_reference(runtime, datetime(2026, 8, 5, 10, 0, tzinfo=shanghai))
        self.assertTrue(uploader.bridge_reference_matches_target(reference, captured_at))
        self.assertEqual((reference or (None, None, None))[1].market_data_type, uploader.HISTORICAL_BID_ASK_MARKET_DATA_TYPE)

    def test_remote_tws_is_never_polled_before_configured_shanghai_open(self) -> None:
        shanghai = ZoneInfo("Asia/Shanghai")
        self.assertFalse(uploader.tws_collection_open(datetime(2026, 8, 5, 8, 59, tzinfo=shanghai), uploader.TWS_DEFAULT_OPEN_TIME))
        self.assertTrue(uploader.tws_collection_open(datetime(2026, 8, 5, 9, 0, tzinfo=shanghai), uploader.TWS_DEFAULT_OPEN_TIME))
        self.assertTrue(uploader.tws_collection_open(datetime(2026, 8, 5, 14, 59, 59, tzinfo=shanghai), uploader.TWS_DEFAULT_OPEN_TIME))
        self.assertFalse(uploader.tws_collection_open(datetime(2026, 8, 5, 15, 0, tzinfo=shanghai), uploader.TWS_DEFAULT_OPEN_TIME))
        self.assertFalse(uploader.tws_collection_open(datetime(2026, 8, 8, 10, 0, tzinfo=shanghai), uploader.TWS_DEFAULT_OPEN_TIME))

    def test_bridge_target_is_the_last_completed_new_york_close(self) -> None:
        shanghai = ZoneInfo("Asia/Shanghai")
        target = uploader.latest_completed_bridge_target(datetime(2026, 8, 5, 14, 0, tzinfo=shanghai))
        self.assertEqual(target, datetime(2026, 8, 4, 15, 50, tzinfo=ZoneInfo("America/New_York")))
        monday_target = uploader.latest_completed_bridge_target(datetime(2026, 8, 3, 9, 0, tzinfo=shanghai))
        self.assertEqual(monday_target, datetime(2026, 7, 31, 15, 50, tzinfo=ZoneInfo("America/New_York")))

    def test_bridge_window_is_narrowed_around_the_validated_close_midpoint(self) -> None:
        new_york = ZoneInfo("America/New_York")
        for minute in (49, 50, 51):
            self.assertTrue(uploader.bridge_capture_window(datetime(2026, 8, 4, 15, minute, tzinfo=new_york)))
        for minute in (48, 52):
            self.assertFalse(uploader.bridge_capture_window(datetime(2026, 8, 4, 15, minute, tzinfo=new_york)))

    def test_monthly_selection_switches_on_last_tuesday_not_after_close(self) -> None:
        candidates = [
            (date(2026, 7, 28), "JUL"),
            (date(2026, 8, 25), "AUG"),
            (date(2026, 9, 29), "SEP"),
        ]
        self.assertEqual(uploader.select_nifty_monthly_contract(candidates, date(2026, 7, 27)), "JUL")
        self.assertEqual(uploader.select_nifty_monthly_contract(candidates, date(2026, 7, 28)), "AUG")
        self.assertEqual(uploader.select_nifty_monthly_contract(candidates, date(2026, 8, 24)), "AUG")
        self.assertEqual(uploader.select_nifty_monthly_contract(candidates, date(2026, 8, 25)), "SEP")

    def test_roll_basis_aligns_new_contract_to_old_reference_scale(self) -> None:
        shanghai = ZoneInfo("Asia/Shanghai")
        captured_at = datetime(2026, 7, 27, 12, 30, tzinfo=shanghai)
        old = uploader.MarketQuote("NIFTY", "JUL", 20_000, 20_002, None, captured_at, "Live")
        new = uploader.MarketQuote("NIFTY", "AUG", 20_100, 20_102, None, captured_at, "Live")
        current = uploader.MarketQuote("NIFTY", "AUG", 20_150, 20_152, None, captured_at, "Live")
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            uploader.record_nifty_roll_sample(runtime, captured_at, date(2026, 7, 28), old, new)
            result = uploader.roll_adjusted_nifty_quote(runtime, current, old)
        self.assertIsNotNone(result)
        adjusted, basis = result or (None, None)
        self.assertEqual(basis.direction, "new_to_old")
        self.assertAlmostEqual(adjusted.bid, 20_150 * 20_000 / 20_102)
        self.assertAlmostEqual(adjusted.ask, 20_152 * 20_002 / 20_100)


if __name__ == "__main__":
    unittest.main()
