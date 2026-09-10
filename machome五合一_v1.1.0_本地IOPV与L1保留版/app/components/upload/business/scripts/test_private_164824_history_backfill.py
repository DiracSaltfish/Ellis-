from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
import json
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import private_164824_history_backfill as history
import private_164824_valuation_uploader as india


class Private164824HistoryBackfillTests(unittest.TestCase):
    def test_official_nav_series_pages_past_the_legacy_220_row_cap(self) -> None:
        end = date(2026, 8, 4)
        requested_pages: list[int] = []

        class Response:
            def __init__(self, payload: dict) -> None:
                self.payload = payload

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(self.payload).encode("utf-8")

        def fake_urlopen(request: object, timeout: float) -> Response:
            url = getattr(request, "full_url")
            page = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["pageIndex"][0])
            requested_pages.append(page)
            nav_day = end - history.timedelta(days=page * 7)
            return Response({
                "Data": {"LSJZList": [{"FSRQ": nav_day.isoformat(), "DWJZ": "1.0000"}]},
                "TotalCount": 999,
            })

        with patch.object(history.urllib.request, "urlopen", side_effect=fake_urlopen):
            rows = history.official_nav_series(1.0, date(2026, 7, 1), end)

        self.assertEqual(requested_pages[-1], 12)
        self.assertLessEqual(rows[0].trading_day, date(2026, 5, 17))

    def test_t_minus_two_uses_fund_nav_trading_dates(self) -> None:
        values = [
            india.OfficialNAV(1.0, date(2026, 7, 30)),
            india.OfficialNAV(1.1, date(2026, 7, 31)),
            india.OfficialNAV(1.2, date(2026, 8, 3)),
        ]
        self.assertEqual(history.t_minus_two_nav(date(2026, 8, 4), values).trading_day, date(2026, 7, 31))
        self.assertEqual(history.t_minus_two_nav(date(2026, 8, 3), values).trading_day, date(2026, 7, 30))

    def test_historical_input_keeps_nifty_bridge_out_of_final_nav_history(self) -> None:
        day = date(2026, 7, 10)
        timestamp = datetime(2026, 7, 9, 20, 0, tzinfo=india.NEW_YORK)
        anchors = tuple(
            india.AnchorObservation(spec.key, spec.label, spec.weight, 100.0, "2026-07-10T00:00:00+08:00", "2026-07-10T00:00:00+08:00", "test", "exact_1m")
            for spec in india.ANCHORS
        )
        prepared = history.PreparedDay(
            day=day,
            nav=india.OfficialNAV(1.0, date(2026, 7, 8)),
            base_fx=SimpleNamespace(rate=7.0, trading_day=date(2026, 7, 8), fetched_at=timestamp),
            current_fx=SimpleNamespace(rate=7.1, trading_day=day, fetched_at=timestamp),
            anchors=anchors,
            close_quote=india.MarketQuote("INDA", "INDA", 100.0, 100.1, None, timestamp, history.HISTORICAL_MARKET_DATA_TYPE),
            close_source=history.HISTORICAL_IB_SOURCE,
            market_prices={"09:30": 1.2},
            coverage=1.0,
            max_gap=0,
        )

        payload = history.historical_input(prepared, "09:30")

        self.assertEqual(payload["ib"]["market_data_type"], history.HISTORICAL_MARKET_DATA_TYPE)
        self.assertNotIn("nifty_bridge", payload["india"])
        self.assertEqual(payload["india"]["base_nav_date"], "2026-07-08")

    def test_historical_input_adds_auditable_nifty_bridge_when_supplied(self) -> None:
        day = date(2026, 5, 26)
        timestamp = datetime(2026, 5, 26, 9, 30, tzinfo=india.SHANGHAI)
        reference_at = datetime(2026, 5, 22, 15, 50, tzinfo=india.NEW_YORK)
        captured_at = datetime(2026, 5, 25, 12, 30, tzinfo=india.SHANGHAI)
        anchors = tuple(
            india.AnchorObservation(spec.key, spec.label, spec.weight, 100.0, "2026-05-22T00:00:00+08:00", "2026-05-22T00:00:00+08:00", "test", "exact_1m")
            for spec in india.ANCHORS
        )
        nifty = india.MarketQuote("NIFTY", "NIFTYM26", 24_000.0, 24_001.0, None, timestamp, history.HISTORICAL_MARKET_DATA_TYPE, history.HISTORICAL_NIFTY_SOURCE)
        adjustment = history.HistoricalNiftyRollAdjustment(
            date(2026, 5, 26),
            captured_at,
            "NIFTYK26",
            "NIFTYM26",
            "new_to_old",
            0.995,
            0.996,
        )
        prepared = history.PreparedDay(
            day=day,
            nav=india.OfficialNAV(1.0, date(2026, 5, 22)),
            base_fx=SimpleNamespace(rate=7.0, trading_day=date(2026, 5, 22), fetched_at=timestamp),
            current_fx=SimpleNamespace(rate=7.1, trading_day=day, fetched_at=timestamp),
            anchors=anchors,
            close_quote=india.MarketQuote("INDA", "INDA", 100.0, 100.1, None, timestamp, history.HISTORICAL_MARKET_DATA_TYPE),
            close_source=history.HISTORICAL_IB_SOURCE,
            market_prices={"09:30": 1.2},
            coverage=1.0,
            max_gap=0,
            nifty_quotes={"09:30": nifty},
            bridge_reference=(
                reference_at,
                india.MarketQuote("INDA", "INDA", 100.0, 100.1, None, reference_at, history.HISTORICAL_MARKET_DATA_TYPE),
                india.MarketQuote("NIFTY", "NIFTYK26", 23_900.0, 23_901.0, None, reference_at, history.HISTORICAL_MARKET_DATA_TYPE, history.HISTORICAL_NIFTY_ROLL_SOURCE),
            ),
            nifty_roll_adjustment=adjustment,
        )

        payload = history.historical_input(prepared, "09:30")

        bridge = payload["india"]["nifty_bridge"]
        self.assertEqual(bridge["contract_selection_version"], india.NIFTY_CONTRACT_SELECTION_VERSION)
        self.assertEqual(bridge["nifty"]["contract"], "NIFTYM26")
        self.assertEqual(bridge["reference_at"], history.common.iso_timestamp(reference_at))
        self.assertEqual(bridge["roll_adjustment"]["direction"], "new_to_old")
        self.assertEqual(bridge["roll_adjustment"]["source"], history.HISTORICAL_NIFTY_ROLL_SOURCE)

    def test_historical_nifty_selection_uses_next_month_from_last_tuesday(self) -> None:
        self.assertEqual(history.historical_nifty_contract_month(date(2026, 7, 27)), "202607")
        self.assertEqual(history.historical_nifty_contract_month(date(2026, 7, 28)), "202608")
        self.assertEqual(history.historical_nifty_contract_month(date(2026, 8, 25)), "202609")

    def test_last_tuesday_reference_uses_new_contract_without_roll_basis(self) -> None:
        market = object.__new__(history.HistoricalINDAMarket)
        selected_days: list[date] = []

        def historical_contract(day: date) -> SimpleNamespace:
            selected_days.append(day)
            month = history.historical_nifty_contract_month(day)
            return SimpleNamespace(localSymbol="NIFTYQ26" if month == "202608" else "NIFTYN26")

        def historical_window(
            contract: SimpleNamespace,
            symbol: str,
            window_start: datetime,
            window_end: datetime,
        ) -> list[india.MarketQuote]:
            observed_at = window_start + timedelta(minutes=1)
            return [india.MarketQuote(
                symbol,
                contract.localSymbol,
                24_000.0,
                24_001.0,
                None,
                observed_at,
                history.HISTORICAL_MARKET_DATA_TYPE,
                history.HISTORICAL_NIFTY_ROLL_SOURCE,
            )]

        market.historical_nifty_contract_for_day = historical_contract
        market.historical_bid_ask_window = historical_window
        market.contract = lambda _: SimpleNamespace(localSymbol="INDA")
        reference = market.nifty_bridge_reference_for_china_day(date(2026, 7, 28))

        self.assertEqual(selected_days, [date(2026, 7, 28)])
        self.assertEqual(reference[2].contract, "NIFTYQ26")
        current = india.MarketQuote(
            "NIFTY", "NIFTYQ26", 24_010.0, 24_011.0, None,
            datetime(2026, 7, 28, 9, 30, tzinfo=india.SHANGHAI),
            history.HISTORICAL_MARKET_DATA_TYPE,
            history.HISTORICAL_NIFTY_SOURCE,
        )
        aligned, adjustment = market.align_nifty_quotes_to_bridge_reference(
            date(2026, 7, 28),
            {"09:30": current},
            reference,
        )
        self.assertIsNone(adjustment)
        self.assertIs(aligned["09:30"], current)
        # No second qualification/query occurred for a basis pair.
        self.assertEqual(selected_days, [date(2026, 7, 28)])

    def test_us_monday_holiday_fallback_reselects_old_reference_contract(self) -> None:
        market = object.__new__(history.HistoricalINDAMarket)
        selected_days: list[date] = []

        def historical_contract(day: date) -> SimpleNamespace:
            selected_days.append(day)
            month = history.historical_nifty_contract_month(day)
            return SimpleNamespace(localSymbol="NIFTYM26" if month == "202606" else "NIFTYK26")

        def historical_window(
            contract: SimpleNamespace,
            symbol: str,
            window_start: datetime,
            window_end: datetime,
        ) -> list[india.MarketQuote]:
            # 2026-05-25 is Memorial Day: both legs are absent, so the
            # reference must fall back to Friday and reselect the old future.
            if window_start.date() == date(2026, 5, 25):
                return []
            observed_at = window_start + timedelta(minutes=1)
            return [india.MarketQuote(
                symbol,
                contract.localSymbol,
                24_000.0,
                24_001.0,
                None,
                observed_at,
                history.HISTORICAL_MARKET_DATA_TYPE,
                history.HISTORICAL_NIFTY_ROLL_SOURCE,
            )]

        market.historical_nifty_contract_for_day = historical_contract
        market.historical_bid_ask_window = historical_window
        market.contract = lambda _: SimpleNamespace(localSymbol="INDA")
        reference = market.nifty_bridge_reference_for_china_day(date(2026, 5, 26))

        self.assertEqual(selected_days, [date(2026, 5, 26), date(2026, 5, 23)])
        self.assertEqual(reference[0], datetime(2026, 5, 22, 15, 50, tzinfo=india.NEW_YORK))
        self.assertEqual(reference[2].contract, "NIFTYK26")
        self.assertEqual(history.nifty_roll_date_between(reference[0], date(2026, 5, 26)), date(2026, 5, 26))

    def test_historical_roll_basis_uses_production_conservative_factors(self) -> None:
        roll_day = date(2026, 7, 28)
        times = [
            datetime(2026, 7, 27, minute=minute, hour=12, tzinfo=india.SHANGHAI)
            for minute in (28, 30, 32)
        ]
        old_samples = [
            india.MarketQuote(
                "NIFTY", "NIFTYN26", 20_000.0 + index, 20_002.0 + index, None,
                observed_at, history.HISTORICAL_MARKET_DATA_TYPE, history.HISTORICAL_NIFTY_ROLL_SOURCE,
            )
            for index, observed_at in enumerate(times)
        ]
        new_samples = [
            india.MarketQuote(
                "NIFTY", "NIFTYQ26", 20_100.0 + index, 20_102.0 + index, None,
                observed_at, history.HISTORICAL_MARKET_DATA_TYPE, history.HISTORICAL_NIFTY_ROLL_SOURCE,
            )
            for index, observed_at in enumerate(times)
        ]

        adjustment = history.historical_nifty_roll_adjustment_from_samples(
            roll_day,
            old_samples,
            new_samples,
            "NIFTYQ26",
            "NIFTYN26",
        )
        self.assertEqual(adjustment.direction, "new_to_old")
        self.assertEqual(adjustment.captured_at, times[1])
        self.assertAlmostEqual(adjustment.bid_factor, 20_001.0 / 20_103.0)
        self.assertAlmostEqual(adjustment.ask_factor, 20_003.0 / 20_101.0)
        self.assertEqual(adjustment.to_payload()["source"], history.HISTORICAL_NIFTY_ROLL_SOURCE)

        current = india.MarketQuote(
            "NIFTY", "NIFTYQ26", 20_150.0, 20_152.0, None,
            datetime(2026, 7, 28, 9, 30, tzinfo=india.SHANGHAI),
            history.HISTORICAL_MARKET_DATA_TYPE,
            history.HISTORICAL_NIFTY_SOURCE,
        )
        adjusted = history.apply_historical_nifty_roll_adjustment({"09:30": current}, adjustment)["09:30"]
        self.assertAlmostEqual(adjusted.bid, 20_150.0 * 20_001.0 / 20_103.0)
        self.assertAlmostEqual(adjusted.ask, 20_152.0 * 20_003.0 / 20_101.0)
        self.assertIsNone(adjusted.last)

        reverse = history.historical_nifty_roll_adjustment_from_samples(
            roll_day,
            old_samples,
            new_samples,
            "NIFTYN26",
            "NIFTYQ26",
        )
        self.assertEqual(reverse.direction, "old_to_new")
        self.assertAlmostEqual(reverse.bid_factor, 20_101.0 / 20_003.0)
        self.assertAlmostEqual(reverse.ask_factor, 20_103.0 / 20_001.0)

    def test_historical_roll_basis_fails_closed_without_exact_audited_window(self) -> None:
        roll_day = date(2026, 7, 28)
        valid_at = datetime(2026, 7, 27, 12, 30, tzinfo=india.SHANGHAI)

        def sample(
            contract: str,
            observed_at: datetime,
            market_data_type: str = history.HISTORICAL_MARKET_DATA_TYPE,
            source: str = history.HISTORICAL_NIFTY_ROLL_SOURCE,
        ) -> india.MarketQuote:
            return india.MarketQuote("NIFTY", contract, 20_000.0, 20_002.0, None, observed_at, market_data_type, source)

        with self.subTest("missing leg"):
            with self.assertRaisesRegex(history.SourceUnavailableError, "no old-contract"):
                history.historical_nifty_roll_adjustment_from_samples(
                    roll_day, [], [sample("NIFTYQ26", valid_at)], "NIFTYQ26", "NIFTYN26"
                )
        with self.subTest("no synchronized minute"):
            with self.assertRaisesRegex(history.SourceUnavailableError, "no exact common"):
                history.historical_nifty_roll_adjustment_from_samples(
                    roll_day,
                    [sample("NIFTYN26", valid_at)],
                    [sample("NIFTYQ26", valid_at + timedelta(minutes=1))],
                    "NIFTYQ26",
                    "NIFTYN26",
                )
        with self.subTest("wrong capture window"):
            wrong_at = datetime(2026, 7, 27, 12, 27, tzinfo=india.SHANGHAI)
            with self.assertRaisesRegex(history.SourceUnavailableError, "outside the exact"):
                history.historical_nifty_roll_adjustment_from_samples(
                    roll_day,
                    [sample("NIFTYN26", wrong_at)],
                    [sample("NIFTYQ26", wrong_at)],
                    "NIFTYQ26",
                    "NIFTYN26",
                )
        with self.subTest("non-audited quote type"):
            with self.assertRaisesRegex(history.SourceUnavailableError, "requires audited"):
                history.historical_nifty_roll_adjustment_from_samples(
                    roll_day,
                    [sample("NIFTYN26", valid_at, "Delayed", "IBKR_TWS")],
                    [sample("NIFTYQ26", valid_at)],
                    "NIFTYQ26",
                    "NIFTYN26",
                )

    def test_previous_weekday_skips_weekend(self) -> None:
        friday = datetime(2026, 7, 3, 15, 50, tzinfo=india.NEW_YORK)
        self.assertEqual(history.previous_weekday(friday), datetime(2026, 7, 2, 15, 50, tzinfo=india.NEW_YORK))
        monday = datetime(2026, 7, 6, 15, 50, tzinfo=india.NEW_YORK)
        self.assertEqual(history.previous_weekday(monday), friday)

    def test_minute_quality_checks_only_continuous_china_sessions(self) -> None:
        day = date(2026, 7, 10)
        prices = {minute: 1.0 for minute in history.china_session_minutes(day)}
        prices.pop("10:00")
        coverage, max_gap = history.minute_quality(prices, day)
        self.assertLess(coverage, 1.0)
        self.assertEqual(max_gap, 1)


if __name__ == "__main__":
    unittest.main()
