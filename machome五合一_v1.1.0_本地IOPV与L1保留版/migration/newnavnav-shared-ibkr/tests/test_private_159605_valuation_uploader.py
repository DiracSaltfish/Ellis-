#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(__file__))

import private_159605_valuation_uploader as uploader  # noqa: E402


def component_xml(identifier: str, source: str, name: str, shares: int) -> str:
    return f"""
    <Component>
      <UnderlyingSecurityID>{identifier}</UnderlyingSecurityID>
      <UnderlyingSecurityIDSource>{source}</UnderlyingSecurityIDSource>
      <UnderlyingSymbol>{name}</UnderlyingSymbol>
      <ComponentShare>{shares}.00</ComponentShare>
      <SubstituteFlag>1</SubstituteFlag>
    </Component>"""


def pcf_xml(*, creation: str = "Y") -> bytes:
    hk = "".join(component_xml(str(700 + index), "103", f"港股{index}", index + 1) for index in range(23))
    us = "".join(component_xml(symbol, "9999", symbol, index + 1) for index, symbol in enumerate(("BZ", "PDD", "QFIN", "TAL", "TME", "VIPS", "YMM")))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<PCFFile xmlns="http://ts.szse.cn/Fund">
  <SecurityID>159605</SecurityID>
  <CreationRedemptionUnit>1000000.00</CreationRedemptionUnit>
  <EstimateCashComponent>-114.02</EstimateCashComponent>
  <NAVperCU>813588.83</NAVperCU>
  <Creation>{creation}</Creation>
  <Redemption>Y</Redemption>
  <TradingDay>20260710</TradingDay>
  <PreTradingDay>20260708</PreTradingDay>
  <TotalRecordNum>31</TotalRecordNum>
  <Components>
    <Component>
      <UnderlyingSecurityID>159900</UnderlyingSecurityID>
      <UnderlyingSecurityIDSource>102</UnderlyingSecurityIDSource>
      <UnderlyingSymbol>申赎现金</UnderlyingSymbol>
      <ComponentShare>0.00</ComponentShare>
    </Component>{hk}{us}
  </Components>
</PCFFile>
""".encode("utf-8")


class Private159605UploaderTests(unittest.TestCase):
    def test_hk_ib_contract_symbol_removes_pcf_padding_only_for_ib_lookup(self) -> None:
        hk = uploader.Component(symbol="0700", name="腾讯控股", market="HK", currency="HKD", quantity=10)
        us = uploader.Component(symbol="BZ", name="BZ", market="US", currency="USD", quantity=10)

        self.assertEqual(uploader.ib_contract_symbol(hk), "700")
        self.assertEqual(uploader.ib_contract_symbol(us), "BZ")

    def test_parser_keeps_30_tradable_components_and_normalizes_hk_codes(self) -> None:
        day = date(2026, 7, 10)
        pcf = uploader.parse_pcf_xml(pcf_xml(), uploader.pcf_url_for_day(day), day)

        self.assertEqual(pcf.security_id, "159605")
        self.assertEqual(pcf.creation, "Y")
        self.assertEqual(pcf.redemption, "Y")
        self.assertEqual(pcf.component_count, 30)
        self.assertEqual(sum(component.market == "HK" for component in pcf.components), 23)
        self.assertEqual(sum(component.market == "US" for component in pcf.components), 7)
        self.assertEqual(pcf.components[0].symbol, "0700")
        self.assertEqual(pcf.components[-1].symbol, "YMM")
        self.assertEqual(pcf.estimate_cash_component_cny, -114.02)

    def test_payload_has_separate_dual_fx_and_exact_component_quotes(self) -> None:
        now = datetime(2026, 7, 10, 10, 0, tzinfo=uploader.common.SHANGHAI)
        day = now.date()
        pcf = uploader.parse_pcf_xml(pcf_xml(), uploader.pcf_url_for_day(day), day)
        rates = (
            uploader.FXQuote("USD/CNY", 7.0, day, "10:00", now),
            uploader.FXQuote("HKD/CNY", 0.87, day, "10:00", now),
        )
        quotes = tuple(
            uploader.MarketQuote(
                symbol=component.symbol,
                market=component.market,
                currency=component.currency,
                bid=10.0,
                ask=10.1,
                last=10.05,
                market_data_type="Live",
                observed_at=now,
            )
            for component in pcf.components
        )

        payload = uploader.build_private_payload(pcf, rates, quotes, generated_at=now)

        self.assertEqual(payload["symbol"], "SZ159605")
        self.assertEqual(payload["model_version"], uploader.MODEL_VERSION)
        self.assertEqual(payload["pcf"]["component_count"], 30)
        self.assertEqual(payload["pcf"]["components"][0]["symbol"], "0700")
        self.assertEqual([item["pair"] for item in payload["fx_rates"]], ["USD/CNY", "HKD/CNY"])
        self.assertEqual(len(payload["market_quotes"]), 30)

    def test_cfets_parser_requires_both_required_pairs(self) -> None:
        now = datetime(2026, 7, 10, 10, 1, tzinfo=uploader.common.SHANGHAI)
        rates = uploader.parse_cfets_rates(
            {
                "records": [
                    {"ccyPair": "USD/CNY", "dealDate": "2026-07-10", "rateOf10hour": "7.0001", "rateOf11hour": "7.0002"},
                    {"ccyPair": "HKD/CNY", "dealDate": "2026-07-10", "rateOf10hour": "0.8701", "rateOf11hour": "0.8702"},
                ]
            },
            now.date(),
            now,
        )
        self.assertEqual([rate.pair for rate in rates], ["USD/CNY", "HKD/CNY"])
        self.assertEqual([rate.quote_time for rate in rates], ["11:00", "11:00"])


if __name__ == "__main__":
    unittest.main()
