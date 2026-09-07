#!/usr/bin/env python3
"""Focused regression tests for the shared China-internet PCF collectors."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import private_china_internet_history_backfill as history  # noqa: E402
import private_china_internet_valuation_uploader as valuation  # noqa: E402


def component(symbol: str, market: str, currency: str) -> valuation.Component:
    return valuation.Component(symbol, symbol, market, currency, 1.0)


class SharedPCFCollectorTest(unittest.TestCase):
    def test_component_map_reuses_identical_market_symbol(self) -> None:
        config = valuation.FUND_BY_SYMBOL["SZ159605"]
        shared = component("0700", "HK", "HKD")
        first = valuation.PCF(config, date(2026, 7, 10), None, "Y", "Y", 1_000_000, 0, 1, (shared,), "https://example/one", "a" * 64)
        second = valuation.PCF(config, date(2026, 7, 10), None, "Y", "Y", 1_000_000, 0, 1, (shared, component("BABA", "US", "USD")), "https://example/two", "b" * 64)
        values = valuation.component_map((first, second))
        self.assertEqual(set(values), {("HK", "0700"), ("US", "BABA")})

    def test_hk_component_uses_sina_last_and_remains_non_live(self) -> None:
        item = component("0700", "HK", "HKD")
        checked_at = datetime(2026, 8, 13, 10, 0, tzinfo=valuation.common.SHANGHAI)
        quote = valuation.quote_from_sina(item, {
            "source": "sina_hk",
            "price": 601.5,
            "quote_date": "2026-08-13",
            "quote_time": "10:00:00",
        }, checked_at)
        self.assertEqual(valuation.sina_component_symbol(item), "00700")
        self.assertIsNotNone(quote)
        self.assertEqual((quote.bid, quote.ask, quote.last), (601.5, 601.5, 601.5))
        self.assertEqual(quote.market_data_type, "SinaLast")
        self.assertEqual(quote.stream_checked_at, checked_at)

    def test_manager_complete_513050_archive_is_accepted(self) -> None:
        raw = b"""<?xml version='1.0'?><SSEPortfolioCompositionFile>
<FundInstrumentID>513050</FundInstrumentID><TradingDay>2026-06-05</TradingDay>
<CreationRedemptionUnit>1000000</CreationRedemptionUnit><EstimatedCashComponent>-1</EstimatedCashComponent>
<NAVperCU>1000000</NAVperCU><RecordNum>2</RecordNum><Creation>1</Creation><Redemption>1</Redemption>
<BackfillSourceURL>https://api.efunds.com.cn/pcf</BackfillSourceURL><BackfillDataGrade>\xe5\x9f\xba\xe9\x87\x91\xe5\x85\xac\xe5\x8f\xb8\xe5\xae\x8c\xe6\x95\xb4 PCF</BackfillDataGrade>
<ComponentList><Component><SecurityID>700</SecurityID><SecurityName>Tencent</SecurityName><ComponentVolume>2</ComponentVolume><Market>\xe9\xa6\x99\xe6\xb8\xaf\xe8\x81\x94\xe5\x90\x88\xe4\xba\xa4\xe6\x98\x93\xe6\x89\x80</Market></Component>
<Component><SecurityID>BABA</SecurityID><SecurityName>Alibaba</SecurityName><ComponentVolume>3</ComponentVolume><Market>\xe5\x85\xb6\xe4\xbb\x96</Market></Component></ComponentList>
</SSEPortfolioCompositionFile>"""
        config = valuation.FUND_BY_SYMBOL["SH513050"]
        pcf = history.parse_manager_513050_pcf(config, raw, Path("513050.xml"), date(2026, 6, 5))
        self.assertEqual([(item.market, item.symbol) for item in pcf.components], [("HK", "0700"), ("US", "BABA")])

    def test_non_pcf_513220_history_is_never_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "513220").mkdir()
            (root / "513220" / "20260605.xml").write_text("<SSEPortfolioCompositionFile/>", encoding="utf-8")
            with self.assertRaisesRegex(history.SourceUnavailableError, "no dated complete PCF"):
                history.load_archived_pcf(root, valuation.FUND_BY_SYMBOL["SH513220"], date(2026, 6, 5))


if __name__ == "__main__":
    unittest.main()
