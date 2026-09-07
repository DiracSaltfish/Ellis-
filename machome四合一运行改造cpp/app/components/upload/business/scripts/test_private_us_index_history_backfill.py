#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from argparse import Namespace
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import private_us_index_history_backfill as backfill


class PrivateUSIndexHistoryBackfillTests(unittest.TestCase):
    def test_replace_existing_is_explicit_and_disabled_by_default(self) -> None:
        self.assertFalse(backfill.parser().parse_args([]).replace_existing)
        self.assertTrue(backfill.parser().parse_args(["--replace-existing"]).replace_existing)

    def test_calendar_front_contract_changes_after_quarterly_expiry(self) -> None:
        self.assertEqual(backfill.calendar_front_month(date(2026, 6, 19)), "202606")
        self.assertEqual(backfill.calendar_front_month(date(2026, 6, 20)), "202609")
        self.assertEqual(backfill.calendar_front_month(date(2026, 12, 19)), "202703")
        self.assertEqual(backfill.front_contract_month(date(2026, 7, 31), backfill.index.DAX_FAMILY), "202609")

    def test_nikkei_front_contract_rolls_to_nearest_month_after_ose_expiry(self) -> None:
        self.assertEqual(backfill.front_contract_month(date(2026, 6, 11), backfill.index.NIKKEI225_FAMILY), "202606")
        self.assertEqual(backfill.front_contract_month(date(2026, 6, 12), backfill.index.NIKKEI225_FAMILY), "202607")

    def test_nikkei_proxy_session_ends_at_1445_shanghai(self) -> None:
        self.assertTrue(backfill.is_proxy_session(datetime(2026, 7, 10, 14, 45, tzinfo=backfill.SHANGHAI), backfill.index.NIKKEI225_FAMILY))
        self.assertFalse(backfill.is_proxy_session(datetime(2026, 7, 10, 14, 46, tzinfo=backfill.SHANGHAI), backfill.index.NIKKEI225_FAMILY))

    def test_daily_bar_date_from_ib_is_preserved(self) -> None:
        self.assertEqual(backfill.parse_daily_bar_day(date(2026, 5, 26)), date(2026, 5, 26))

    def test_sse_history_without_local_archive_fails_closed(self) -> None:
        class Store:
            def path(self, fund, day):  # noqa: ANN001
                return Path("/definitely/not/a/pcf.xml")

            def fetch(self, fund, day, family):  # noqa: ANN001
                raise AssertionError("historical SSE must not request today's endpoint")

        with self.assertRaisesRegex(backfill.SourceUnavailableError, "SSE 历史 PCF"):
            backfill.load_dated_pcf(Store(), backfill.index.NASDAQ_FAMILY, backfill.index.NQ_FUNDS[0], date(2026, 7, 10))

    def test_historical_pcf_keeps_dated_component_count_without_live_hard_gate(self) -> None:
        captured = {}

        class Store:
            def path(self, fund, day):  # noqa: ANN001
                return Path("/tmp/nonexistent-szse-history-pcf.xml")

            def fetch(self, fund, day, family):  # noqa: ANN001
                captured["fund"] = fund
                return "pcf"

        result = backfill.load_dated_pcf(Store(), backfill.index.NASDAQ_FAMILY, backfill.index.NQ_FUNDS[5], date(2026, 7, 10))
        self.assertEqual(result, "pcf")
        self.assertEqual(captured["fund"].symbol, "SZ159501")
        self.assertEqual(captured["fund"].component_count, 0)

    def test_future_bid_ask_cache_is_keyed_by_conid_date_and_side(self) -> None:
        spec = backfill.ResolvedFuture("NQ", "202609", 12345, "NQU6", "20260918", 20.0)
        day = date(2026, 7, 10)
        with tempfile.TemporaryDirectory() as root:
            runtime = Path(root)
            for what, value in (("BID", 20000.0), ("ASK", 20001.0)):
                backfill.atomic_json(backfill.futures_cache_path(runtime, spec, day, what), {
                    "schema_version": backfill.RAW_CACHE_SCHEMA, "reference": "NQ", "contract": spec.to_payload(), "day": day.isoformat(),
                    "what": what, "bar_size": "1 min", "request_window_days": 1, "use_rth": False, "rows": {"09:30": value},
                })
            bid = backfill.cached_future_series(None, runtime, spec, [day], "BID", 0)
            ask = backfill.cached_future_series(None, runtime, spec, [day], "ASK", 0)
            self.assertEqual(bid[day], {"09:30": 20000.0})
            self.assertEqual(ask[day], {"09:30": 20001.0})

    def test_germany_close_cache_is_versioned_and_uses_xetra_1735_anchor(self) -> None:
        day = date(2026, 7, 30)
        spec = backfill.ResolvedFuture(
            "DAX", "202609", 655095900, "FDXMU6", "20260918", 5.0,
            exchange="EUREX", currency="EUR", timezone="Europe/Berlin",
        )

        class FakeIB:
            def qualifyContracts(self, contract):  # noqa: ANN001
                contract.conId = spec.con_id
                return [contract]

        berlin = backfill.ZoneInfo("Europe/Berlin")
        anchor = backfill.index.XetraCloseAnchor(
            day, 25_653.0, 25_693.0,
            datetime(2026, 7, 30, 17, 29, tzinfo=berlin),
            datetime(2026, 7, 30, 17, 34, tzinfo=berlin),
        )
        with tempfile.TemporaryDirectory() as root:
            runtime = Path(root)
            old_path = backfill.close_cache_path(runtime, spec, day)
            backfill.atomic_json(old_path, {"day": day.isoformat(), "close": 99_999.0})
            new_path = backfill.close_cache_path(runtime, spec, day, backfill.index.DAX_FAMILY)
            self.assertIn("XETRA_1735_CLOSE_V2", new_path.name)
            with patch.object(backfill.index, "xetra_close_auction_anchor", return_value=anchor):
                got = backfill.future_regular_close(
                    FakeIB(), runtime, spec, day, 0, backfill.index.DAX_FAMILY,
                )
            self.assertEqual(got, 25_693.0)
            payload = backfill.load_json(new_path)
            self.assertEqual(payload["anchor"]["comparison_close_1730"], 25_653.0)
            self.assertEqual(payload["anchor"]["method"], "FDXM_TRADES_1M_ENDING_XETRA_1735_V2")

    def test_germany_replace_replays_private_only_dates_but_other_families_do_not(self) -> None:
        public = [date(2026, 6, 22)]
        existing = {date(2026, 6, 18), date(2026, 6, 22)}
        self.assertEqual(
            backfill.replay_target_days(
                backfill.index.DAX_FAMILY, public, existing, None, None, True,
            ),
            [date(2026, 6, 18), date(2026, 6, 22)],
        )
        self.assertEqual(
            backfill.replay_target_days(
                backfill.index.NASDAQ_FAMILY, public, existing, None, None, True,
            ),
            [date(2026, 6, 22)],
        )

    def test_germany_can_recover_only_market_prices_from_expired_private_day(self) -> None:
        day = date(2026, 6, 18)
        responses = [
            {"rows": []},
            {"rows": [{
                "minute": "2026-06-18T09:30:00+08:00",
                "market_price": 1.295,
                "basket_bid_nav": 999.0,
                "buy_direction_premium_rate": 999.0,
            }]},
        ]
        with tempfile.TemporaryDirectory() as root, patch.object(
            backfill, "source_json", side_effect=responses,
        ):
            prices = backfill.cached_public_prices(
                Path(root), "https://example.test", "SZ159561", day, 1,
                backfill.index.DAX_FAMILY,
            )
            cached = backfill.load_json(
                Path(root) / "public_minutes" / "germany" / "SZ159561" / "20260618.json",
            )
        self.assertEqual(prices, {"09:30": 1.295})
        self.assertEqual(cached["source"], "private_history_market_price_recovery")

    def test_quality_gate_rejects_long_missing_interval(self) -> None:
        prices = {f"09:{minute:02d}": 1.0 for minute in range(30, 40)}
        bids = {minute: 20000.0 for minute in prices if minute not in {"09:33", "09:34", "09:35", "09:36"}}
        asks = {minute: 20001.0 for minute in bids}
        with self.assertRaisesRegex(backfill.SourceUnavailableError, "max_gap=4"):
            backfill.quality_check(prices, bids, asks, 0.5, 3)

    def test_upload_chunks_respect_payload_bytes_before_api_row_limit(self) -> None:
        rows = [{"minute": f"2026-07-10T09:{30 + index:02d}:00+08:00", "input": {"pcf": "x" * 200}} for index in range(3)]
        chunks = list(backfill.payload_chunks(rows, row_limit=500, byte_limit=650))
        self.assertEqual([len(chunk) for chunk in chunks], [2, 1])
        self.assertTrue(all(len(__import__("json").dumps({"rows": chunk}).encode("utf-8")) <= 650 for chunk in chunks))

    def test_replace_upload_sends_one_complete_gzip_day(self) -> None:
        rows = [
            {"minute": "2026-07-31T09:30:00+08:00"},
            {"minute": "2026-07-31T09:31:00+08:00"},
        ]
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"ok":true,"imported":2}'

        def open_request(request, *_args):  # noqa: ANN001
            captured["request"] = request
            return Response()

        args = Namespace(
            replace_existing=True,
            server="https://example.test",
            token="secret",
            timeout=10,
            origin_ip="",
            origin_tls_insecure=False,
            origin_ca_file="",
            batch_size=1,
            max_payload_bytes=100,
        )
        with patch.object(backfill.common, "open_server_request", side_effect=open_request):
            imported = backfill.upload_rows(args, "SZ159561", rows)
        request = captured["request"]
        payload = json.loads(gzip.decompress(request.data).decode("utf-8"))
        self.assertEqual(imported, 2)
        self.assertEqual(request.get_header("Content-encoding"), "gzip")
        self.assertTrue(payload["replace_day"])
        self.assertEqual(payload["rows"], rows)

    def test_fixed_proxy_payload_marks_latest_pcf_replay(self) -> None:
        fund = backfill.index.NQ_FUNDS[0]
        pcf = backfill.index.PCF(
            fund=fund, trading_day=date(2026, 7, 10), pre_trading_day=date(2026, 7, 9), creation="Y", redemption="Y",
            estimate_cash_component_cny=1.0, previous_cash_component_cny=2.0, nav_per_cu=100.0,
            components=(backfill.index.Component("TEST", "Test", 1.0),), source_url="https://example.test/pcf", sha256="a" * 64,
        )
        prepared = backfill.PreparedDay(
            backfill.index.NASDAQ_FAMILY, pcf, date(2026, 6, 2), {"09:30": 1.0},
            backfill.HistoricalFXQuote("USD/CNY", 7.0, date(2026, 6, 2), "16:30", "CFETS_USD_CNY_SPOT_CLOSE_1630", datetime(2026, 6, 2, 16, 30, tzinfo=backfill.SHANGHAI)),
            backfill.index.CentralParity(7.0, date(2026, 7, 9), datetime(2026, 7, 10, 9, 0)),
            backfill.ResolvedFuture("NQ", "202606", 1, "NQM6", "20260618", 20.0), fixed_proxy=True, fixed_contracts=0.5,
        )
        payload = backfill.historical_payload(prepared, "09:30", 20_000.0, 20_001.0, 0.5)
        self.assertTrue(payload["pcf"]["historical_fixed_proxy"])
        self.assertEqual(payload["pcf"]["trading_day"], "2026-07-10")

    def test_nikkei_uses_its_own_historical_replay_chain(self) -> None:
        self.assertEqual(
            {family.key for family in backfill.selected_funds(Namespace(families="all"))},
            {"nasdaq", "sp500", "nikkei225", "germany"},
        )
        self.assertEqual(
            backfill.selected_funds(Namespace(families="nikkei225")),
            (backfill.index.NIKKEI225_FAMILY,),
        )

    def test_cached_historical_nikkei_fx_uses_jpy_pair_and_1600_quote(self) -> None:
        day = date(2026, 7, 10)
        with tempfile.TemporaryDirectory() as root:
            runtime = Path(root)
            backfill.atomic_json(runtime / "cfets_jpy_cny_reference" / "20260710-1600.json", {
                "trading_day": "2026-07-10", "quote_time": "16:00", "rate": 0.041924,
            })
            quote = backfill.historical_fx_for_day(runtime, backfill.index.NIKKEI225_FAMILY, day, 1, "")
        self.assertEqual((quote.pair, quote.quote_time), ("JPY/CNY", "16:00"))
        self.assertAlmostEqual(quote.rate, 0.041924, places=12)

    def test_cached_historical_germany_fx_uses_eur_pair_and_1600_quote(self) -> None:
        day = date(2026, 7, 31)
        with tempfile.TemporaryDirectory() as root:
            runtime = Path(root)
            backfill.atomic_json(runtime / "cfets_eur_cny_reference" / "20260731-1600.json", {
                "trading_day": "2026-07-31", "quote_time": "16:00", "rate": 7.7678,
            })
            quote = backfill.historical_fx_for_day(runtime, backfill.index.DAX_FAMILY, day, 1, "")
        self.assertEqual((quote.pair, quote.quote_time), ("EUR/CNY", "16:00"))
        self.assertAlmostEqual(quote.rate, 7.7678, places=12)


if __name__ == "__main__":
    unittest.main()
