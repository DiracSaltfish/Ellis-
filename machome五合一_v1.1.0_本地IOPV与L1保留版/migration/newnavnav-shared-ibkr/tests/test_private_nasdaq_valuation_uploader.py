#!/usr/bin/env python3
"""Regression tests for the non-actionable Nasdaq PCF/NQ collector."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_nasdaq_valuation_uploader as uploader


def sz_components(count: int) -> str:
    rows = [
        "<Component><UnderlyingSecurityID>159900</UnderlyingSecurityID><UnderlyingSecurityIDSource>102</UnderlyingSecurityIDSource><UnderlyingSymbol>现金</UnderlyingSymbol><ComponentShare>0</ComponentShare></Component>"
    ]
    for index in range(count):
        ticker = f"NQ{index:03d}"
        rows.append(
            "<Component>"
            f"<UnderlyingSecurityID>{ticker}</UnderlyingSecurityID><UnderlyingSecurityIDSource>9999</UnderlyingSecurityIDSource>"
            f"<UnderlyingSymbol>{ticker}</UnderlyingSymbol><ComponentShare>{index + 1}</ComponentShare>"
            "</Component>"
        )
    return "".join(rows)


def sse_components(count: int) -> str:
    rows = []
    for index in range(count):
        ticker = f"NQ{index:03d}"
        rows.append(
            "<Component>"
            f"<InstrumentID>{ticker}</InstrumentID><InstrumentName>{ticker}</InstrumentName><Quantity>{index + 1}</Quantity><UnderlyingSecurityID>9999</UnderlyingSecurityID>"
            "</Component>"
        )
    return "".join(rows)


def sp500_sse_components(count: int, positive_count: int) -> str:
    rows = []
    for index in range(count):
        ticker = f"SP{index:03d}"
        quantity = 1 if index < positive_count else 0
        rows.append(
            "<Component>"
            f"<InstrumentID>{ticker}</InstrumentID><InstrumentName>{ticker}</InstrumentName><Quantity>{quantity}</Quantity><UnderlyingSecurityID>9999</UnderlyingSecurityID>"
            "</Component>"
        )
    return "".join(rows)


def nikkei_sse_component(ticker: str, quantity: int) -> str:
    return (
        "<Component>"
        f"<InstrumentID>{ticker}</InstrumentID><InstrumentName>日经225</InstrumentName><Quantity>{quantity}</Quantity><UnderlyingSecurityID>9999</UnderlyingSecurityID>"
        "</Component>"
    )


class PrivateNasdaqUploaderTests(unittest.TestCase):
    def test_coefficient_cache_is_reused_only_for_exact_same_day_pcf(self) -> None:
        fund = next(item for item in uploader.NIKKEI225_FUNDS if item.symbol == "SH513520")
        pcf = uploader.PCF(
            fund=fund,
            trading_day=date(2026, 9, 2),
            pre_trading_day=date(2026, 9, 1),
            creation="Y",
            redemption="Y",
            estimate_cash_component_cny=-936.18,
            previous_cash_component_cny=-936.18,
            nav_per_cu=1111109.88,
            components=(uploader.Component("1321", "日经225", 378, "JP", "JPY"),),
            source_url="https://example.test/513520.xml",
            sha256="a" * 64,
        )
        parity = uploader.CentralParity(
            0.041863,
            pcf.pre_trading_day,
            datetime(2026, 9, 2, 9, 0, tzinfo=uploader.SHANGHAI),
        )
        coefficient = uploader.nq_contract_equivalent(pcf, parity, 66_800, uploader.NIKKEI225_FAMILY)
        with tempfile.TemporaryDirectory() as raw:
            store = uploader.CoefficientStore(Path(raw))
            self.assertIsNone(store.load(pcf, uploader.NIKKEI225_FAMILY))
            self.assertIsNone(store.load_parity(pcf.pre_trading_day, uploader.NIKKEI225_FAMILY))
            store.save(pcf, parity, 66_800, coefficient, uploader.NIKKEI225_FAMILY)
            self.assertAlmostEqual(store.load(pcf, uploader.NIKKEI225_FAMILY) or 0, coefficient, places=12)
            self.assertAlmostEqual(
                (store.load_parity(pcf.pre_trading_day, uploader.NIKKEI225_FAMILY) or parity).rate,
                parity.rate,
                places=12,
            )
            self.assertIsNone(store.load_parity(date(2026, 8, 31), uploader.NIKKEI225_FAMILY))
            self.assertIsNone(store.load_parity(pcf.pre_trading_day, uploader.DAX_FAMILY))

            changed_pcf = uploader.PCF(**{**pcf.__dict__, "sha256": "b" * 64})
            next_day_pcf = uploader.PCF(
                **{
                    **pcf.__dict__,
                    "trading_day": date(2026, 9, 3),
                    "pre_trading_day": date(2026, 9, 2),
                }
            )
            self.assertIsNone(store.load(changed_pcf, uploader.NIKKEI225_FAMILY))
            self.assertIsNone(store.load(next_day_pcf, uploader.NIKKEI225_FAMILY))

    def test_live_etf_payload_uses_cfets_spot_not_safe_central_parity(self) -> None:
        now = datetime(2026, 9, 2, 10, 0, tzinfo=uploader.SHANGHAI)
        fund = next(item for item in uploader.NIKKEI225_FUNDS if item.symbol == "SH513520")
        pcf = uploader.PCF(
            fund=fund,
            trading_day=now.date(),
            pre_trading_day=date(2026, 9, 1),
            creation="Y",
            redemption="Y",
            estimate_cash_component_cny=0,
            previous_cash_component_cny=0,
            nav_per_cu=1_000_000,
            components=(uploader.Component("1321", "日经225", 1, "JP", "JPY"),),
            source_url="https://example.test/513520.xml",
            sha256="a" * 64,
        )
        cfets = uploader.common.CFETSSpotQuote("JPY/CNY", 0.0419, now.date(), "10:00", now)
        quote = uploader.NQQuote("N225M", 64_000, 64_005, 64_002.5, now, "Live", now)
        value = uploader.payload(pcf, cfets, quote, 3.5, now, uploader.NIKKEI225_FAMILY)
        self.assertEqual(value["fx"]["source"], "CFETS_SPOT_RATE")
        self.assertNotEqual(value["fx"]["source"], uploader.SAFE_SOURCE)

    def test_parse_szse_requires_ndx_and_discards_cash_row(self) -> None:
        raw = (
            "<PCFFile><SecurityID>159659</SecurityID><UnderlyingSecurityID>NDX</UnderlyingSecurityID>"
            "<TradingDay>20260710</TradingDay><PreTradingDay>20260708</PreTradingDay>"
            "<Creation>Y</Creation><Redemption>Y</Redemption><CreationRedemptionUnit>1000000</CreationRedemptionUnit>"
            "<EstimateCashComponent>-2636.32</EstimateCashComponent><CashComponent>-2636.32</CashComponent><NAVperCU>2144465.28</NAVperCU>"
            "<TotalRecordNum>104</TotalRecordNum><Components>" + sz_components(103) + "</Components></PCFFile>"
        ).encode()
        fund = next(item for item in uploader.FUNDS if item.symbol == "SZ159659")
        pcf = uploader.parse_pcf(fund, raw, "https://example.test/159659.xml", date(2026, 7, 10))
        self.assertEqual(len(pcf.components), 103)
        self.assertEqual(pcf.components[0].symbol, "NQ000")
        self.assertEqual(pcf.previous_cash_component_cny, -2636.32)

    def test_parse_sse_513300_retains_redemption_only_switch_and_750k_unit(self) -> None:
        raw = (
            "<SSEPortfolioCompositionFile><FundInstrumentID>513300</FundInstrumentID>"
            "<TradingDay>2026-07-10</TradingDay><PreTradingDay>2026-07-08</PreTradingDay>"
            "<CreationRedemptionSwitch>3</CreationRedemptionSwitch><CreationRedemptionUnit>750000</CreationRedemptionUnit>"
            "<EstimatedCashComponent>1535.82</EstimatedCashComponent><PreCashComponent>-88.24</PreCashComponent><NAVperCU>1857436.49</NAVperCU>"
            "<RecordNumber>102</RecordNumber><ComponentList>" + sse_components(102) + "</ComponentList></SSEPortfolioCompositionFile>"
        ).encode()
        fund = next(item for item in uploader.FUNDS if item.symbol == "SH513300")
        pcf = uploader.parse_pcf(fund, raw, "https://example.test/513300.xml", date(2026, 7, 10))
        self.assertEqual((pcf.creation, pcf.redemption), ("N", "Y"))
        self.assertEqual(len(pcf.components), 102)
        self.assertEqual(pcf.fund.redemption_unit, 750_000)

    def test_contract_equivalent_uses_previous_cash_and_standard_nq_multiplier(self) -> None:
        pcf = uploader.PCF(
            fund=next(item for item in uploader.FUNDS if item.symbol == "SZ159659"),
            trading_day=date(2026, 7, 10), pre_trading_day=date(2026, 7, 8), creation="Y", redemption="Y",
            estimate_cash_component_cny=-2636.32, previous_cash_component_cny=-2636.32, nav_per_cu=2144465.28,
            components=tuple(uploader.Component(f"NQ{index:03d}", f"NQ{index:03d}", 1) for index in range(103)),
            source_url="https://example.test/159659.xml", sha256="a" * 64,
        )
        parity = uploader.CentralParity(7.0, date(2026, 7, 8), datetime(2026, 7, 9, 9, 0, tzinfo=uploader.SHANGHAI))
        got = uploader.nq_contract_equivalent(pcf, parity, 22_000)
        self.assertAlmostEqual(got, (2144465.28 + 2636.32) / (7 * 22_000 * 20), places=12)

    def test_parse_sp500_sse_discards_zero_substitution_rows_and_uses_es_multiplier(self) -> None:
        raw = (
            "<SSEPortfolioCompositionFile><FundInstrumentID>513500</FundInstrumentID>"
            "<TradingDay>2026-07-10</TradingDay><PreTradingDay>2026-07-08</PreTradingDay>"
            "<CreationRedemptionSwitch>1</CreationRedemptionSwitch><CreationRedemptionUnit>1000000</CreationRedemptionUnit>"
            "<EstimatedCashComponent>30426.57</EstimatedCashComponent><PreCashComponent>30347.39</PreCashComponent><NAVperCU>2409292.79</NAVperCU>"
            "<RecordNumber>503</RecordNumber><ComponentList>" + sp500_sse_components(503, 446) + "</ComponentList></SSEPortfolioCompositionFile>"
        ).encode()
        fund = next(item for item in uploader.SP500_FUNDS if item.symbol == "SH513500")
        pcf = uploader.parse_pcf(fund, raw, "https://example.test/513500.xml", date(2026, 7, 10), uploader.SP500_FAMILY)
        self.assertEqual(len(pcf.components), 446)
        parity = uploader.CentralParity(7.0, date(2026, 7, 8), datetime(2026, 7, 9, 9, 0, tzinfo=uploader.SHANGHAI))
        got = uploader.nq_contract_equivalent(pcf, parity, 6_200, uploader.SP500_FAMILY)
        self.assertAlmostEqual(got, (2409292.79 - 30347.39) / (7 * 6_200 * 50), places=12)

    def test_parse_sp500_szse_requires_spxntr(self) -> None:
        raw = (
            "<PCFFile><SecurityID>159612</SecurityID><UnderlyingSecurityID>SPXNTR</UnderlyingSecurityID>"
            "<TradingDay>20260710</TradingDay><PreTradingDay>20260708</PreTradingDay>"
            "<Creation>N</Creation><Redemption>Y</Redemption><CreationRedemptionUnit>1000000</CreationRedemptionUnit>"
            "<EstimateCashComponent>33503.99</EstimateCashComponent><CashComponent>33092.70</CashComponent><NAVperCU>1890758.37</NAVperCU>"
            "<TotalRecordNum>3</TotalRecordNum><Components>" + sz_components(2) + "</Components></PCFFile>"
        ).encode()
        fund = next(item for item in uploader.SP500_FUNDS if item.symbol == "SZ159612")
        pcf = uploader.parse_pcf(fund, raw, "https://example.test/159612.xml", date(2026, 7, 10), uploader.SP500_FAMILY)
        self.assertEqual((pcf.creation, pcf.redemption, len(pcf.components)), ("N", "Y", 2))

    def test_parse_nikkei_wrapper_pcf_and_n225m_contract_equivalent(self) -> None:
        raw = (
            "<SSEPortfolioCompositionFile><FundInstrumentID>513520</FundInstrumentID>"
            "<TradingDay>20260710</TradingDay><PreTradingDay>20260709</PreTradingDay>"
            "<CreationRedemptionSwitch>1</CreationRedemptionSwitch><CreationRedemptionUnit>500000</CreationRedemptionUnit>"
            "<EstimatedCashComponent>-936.18</EstimatedCashComponent><PreCashComponent>-936.18</PreCashComponent><NAVperCU>1111109.88</NAVperCU>"
            "<RecordNumber>1</RecordNumber><ComponentList>" + nikkei_sse_component("1321", 378) + "</ComponentList></SSEPortfolioCompositionFile>"
        ).encode()
        fund = next(item for item in uploader.NIKKEI225_FUNDS if item.symbol == "SH513520")
        pcf = uploader.parse_pcf(fund, raw, "https://example.test/513520.xml", date(2026, 7, 10), uploader.NIKKEI225_FAMILY)
        self.assertEqual((pcf.components[0].symbol, pcf.components[0].market, pcf.components[0].currency), ("1321", "JP", "JPY"))
        parity = uploader.CentralParity(0.041863, date(2026, 7, 9), datetime(2026, 7, 10, 9, 0, tzinfo=uploader.SHANGHAI))
        got = uploader.nq_contract_equivalent(pcf, parity, 66_800, uploader.NIKKEI225_FAMILY)
        self.assertAlmostEqual(got, (1111109.88 + 936.18) / (0.041863 * 66_800 * 100), places=12)

    def test_jpy_cfets_converts_100jpy_quote_to_one_jpy_rate(self) -> None:
        payload = {
            "records": [{
                "ccyPair": "100JPY/CNY", "dealDate": "2026-07-10",
                "rateOf10hour": "4.1936", "rateOf11hour": "4.1985", "rateOf14hour": "4.1957",
                "rateOf15hour": "4.1963", "rateOf16hour": "4.1924",
            }],
        }
        client = uploader.JPYCFETSClient(1, fetch_bytes=lambda *_: json.dumps(payload).encode())
        quote = client.fetch_latest(date(2026, 7, 10))
        self.assertEqual((quote.trading_day, quote.quote_time), (date(2026, 7, 10), "16:00"))
        self.assertAlmostEqual(quote.rate, 0.041924, places=12)
        self.assertEqual(quote.to_payload()["pair"], "JPY/CNY")

    def test_jpy_cfets_historical_cutoff_selects_1600_not_later_quote(self) -> None:
        payload = {
            "records": [{
                "ccyPair": "100JPY/CNY", "dealDate": "2026-07-10",
                "rateOf16hour": "4.1924", "rateOf17hour": "4.1999",
            }],
        }
        client = uploader.JPYCFETSClient(1, fetch_bytes=lambda *_: json.dumps(payload).encode())
        quote = client.fetch_latest(date(2026, 7, 10), max_hour=16)
        self.assertEqual(quote.quote_time, "16:00")
        self.assertAlmostEqual(quote.rate, 0.041924, places=12)

    def test_parse_dax_sse_pcf_keeps_40_eur_components_and_fund_unit(self) -> None:
        raw = (
            "<SSEPortfolioCompositionFile><FundInstrumentID>513030</FundInstrumentID>"
            "<TradingDay>20260731</TradingDay><PreTradingDay>20260729</PreTradingDay>"
            "<CreationRedemptionSwitch>1</CreationRedemptionSwitch><CreationRedemptionUnit>500000</CreationRedemptionUnit>"
            "<EstimatedCashComponent>1969.57</EstimatedCashComponent><PreCashComponent>1969.57</PreCashComponent><NAVperCU>897636.98</NAVperCU>"
            "<RecordNumber>40</RecordNumber><ComponentList>" + sse_components(40) + "</ComponentList></SSEPortfolioCompositionFile>"
        ).encode()
        fund = next(item for item in uploader.DAX_FUNDS if item.symbol == "SH513030")
        pcf = uploader.parse_pcf(fund, raw, "https://example.test/513030.xml", date(2026, 7, 31), uploader.DAX_FAMILY)
        self.assertEqual((len(pcf.components), pcf.fund.redemption_unit), (40, 500_000))
        self.assertTrue(all((item.market, item.currency) == ("DE", "EUR") for item in pcf.components))
        numeric = uploader.component("1COV", "Covestro", 1, uploader.DAX_FAMILY)
        self.assertEqual(numeric.symbol, "1COV")

    def test_eur_cfets_selects_exact_hour_without_unit_conversion(self) -> None:
        payload = {
            "records": [{
                "ccyPair": "EUR/CNY", "dealDate": "2026-07-31",
                "rateOf10hour": "7.7685", "rateOf15hour": "7.7699", "rateOf16hour": "7.7678",
            }],
        }
        client = uploader.EURCFETSClient(1, fetch_bytes=lambda *_: json.dumps(payload).encode())
        quote = client.fetch_latest(date(2026, 7, 31), max_hour=16)
        self.assertEqual((quote.quote_time, quote.to_payload()["pair"]), ("16:00", "EUR/CNY"))
        self.assertAlmostEqual(quote.rate, 7.7678, places=12)

    def test_front_contract_selection_rejects_expired_contracts(self) -> None:
        class Detail:
            def __init__(self, expiry: str) -> None:
                self.contract = type("Contract", (), {"lastTradeDateOrContractMonth": expiry})()

        selected = uploader.select_front_nq_contract(
            [Detail("20260619"), Detail("20260918"), Detail("20261218")],
            datetime(2026, 7, 10, 10, 0, tzinfo=uploader.NEW_YORK),
        )
        self.assertEqual(selected.lastTradeDateOrContractMonth, "20260918")


class IndexFutureSearchContractTest(unittest.TestCase):
    def test_currency_is_not_written_to_local_symbol(self) -> None:
        contract = uploader.future_search_contract(uploader.NASDAQ_FAMILY)

        self.assertEqual(contract.symbol, "NQ")
        self.assertEqual(contract.exchange, "CME")
        self.assertEqual(contract.currency, "USD")
        self.assertEqual(contract.localSymbol, "")
        self.assertEqual(contract.multiplier, "20")

    def test_dax_root_and_multiplier_resolve_mini_dax(self) -> None:
        contract = uploader.future_search_contract(uploader.DAX_FAMILY)

        self.assertEqual(contract.symbol, "DAX")
        self.assertEqual(contract.exchange, "EUREX")
        self.assertEqual(contract.currency, "EUR")
        self.assertEqual(contract.multiplier, "5")

    def test_dax_calibration_uses_bars_ending_at_xetra_1730_and_1735(self) -> None:
        class FakeIB:
            def __init__(self) -> None:
                self.request = None

            def reqHistoricalData(self, contract, **kwargs):  # noqa: ANN001
                self.request = (contract, kwargs)
                return [
                    # 17:29/17:34 Europe/Berlin in July (UTC+2).
                    SimpleNamespace(date=datetime(2026, 7, 30, 15, 29, tzinfo=timezone.utc), close=25_653.0),
                    SimpleNamespace(date=datetime(2026, 7, 30, 15, 34, tzinfo=timezone.utc), close=25_693.0),
                ]

        ib = FakeIB()
        contract = object()
        anchor = uploader.xetra_close_auction_anchor(ib, contract, date(2026, 7, 30))

        self.assertEqual((anchor.close_1730, anchor.close_1735), (25_653.0, 25_693.0))
        self.assertEqual(anchor.bar_start_1735.strftime("%H:%M"), "17:34")
        self.assertEqual(datetime.fromisoformat(anchor.to_payload()["bar_end_1735"]).strftime("%H:%M"), "17:35")
        self.assertEqual(ib.request[1]["barSizeSetting"], "1 min")
        self.assertEqual(ib.request[1]["whatToShow"], "TRADES")
        self.assertFalse(ib.request[1]["useRTH"])
        self.assertEqual(ib.request[1]["endDateTime"].strftime("%H:%M %Z"), "17:36 CEST")

    def test_dax_calibration_rejects_missing_1735_bar(self) -> None:
        class FakeIB:
            def reqHistoricalData(self, *_args, **_kwargs):
                return [SimpleNamespace(
                    date=datetime(2026, 7, 30, 15, 29, tzinfo=timezone.utc), close=25_653.0,
                )]

        with self.assertRaisesRegex(uploader.SourceUnavailableError, "1735"):
            uploader.xetra_close_auction_anchor(FakeIB(), object(), date(2026, 7, 30))


if __name__ == "__main__":
    unittest.main()
