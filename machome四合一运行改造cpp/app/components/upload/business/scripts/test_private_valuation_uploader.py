#!/usr/bin/env python3
from __future__ import annotations

import json
import gzip
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))

import private_valuation_uploader as uploader  # noqa: E402


SHANGHAI = ZoneInfo("Asia/Shanghai")


def pcf_xml(*, estimate_cash: str = "1284.61", include_estimate: bool = True) -> bytes:
    estimate = (
        f"<EstimateCashComponent>{estimate_cash}</EstimateCashComponent>"
        if include_estimate
        else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<PCFFile xmlns="http://ts.szse.cn/Fund">
  <SecurityID>159518</SecurityID>
  <CreationRedemptionUnit>1000000.00</CreationRedemptionUnit>
  {estimate}
  <TradingDay>20260710</TradingDay>
  <PreTradingDay>20260708</PreTradingDay>
  <CashComponent>999999.99</CashComponent>
  <NAVperCU>1099440.21</NAVperCU>
  <Redemption>Y</Redemption>
  <TotalRecordNum>2</TotalRecordNum>
  <Components>
    <Component>
      <UnderlyingSecurityID>159900</UnderlyingSecurityID>
      <UnderlyingSecurityIDSource>102</UnderlyingSecurityIDSource>
      <UnderlyingSymbol>申赎现金</UnderlyingSymbol>
      <ComponentShare>0.00</ComponentShare>
      <CreationCashSubstitute>1207971.15</CreationCashSubstitute>
    </Component>
    <Component>
      <UnderlyingSecurityID>APA</UnderlyingSecurityID>
      <UnderlyingSecurityIDSource>9999</UnderlyingSecurityIDSource>
      <ComponentShare>110.00</ComponentShare>
      <CreationCashSubstitute>999999.99</CreationCashSubstitute>
    </Component>
  </Components>
</PCFFile>
""".encode("utf-8")


class PrivateValuationUploaderTests(unittest.TestCase):
    def test_shared_cfets_spot_client_uses_one_website_snapshot_for_many_pairs(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps({
                    "fx_quotes": {
                        "USD/CNY": {
                            "pair": "USD/CNY", "bid": 6.7208, "ask": 6.7209,
                            "healthy": True, "observed_at": "2026-08-26T10:20:00+08:00",
                            "received_at": "2026-08-26T10:20:01.435945161+08:00",
                        },
                        "100JPY/CNY": {
                            "pair": "100JPY/CNY", "bid": 4.2256, "ask": 4.2257,
                            "healthy": True, "observed_at": "2026-08-26T10:20:00+08:00",
                            "received_at": "2026-08-26T10:20:01+08:00",
                        },
                    },
                }).encode("utf-8")

        client = uploader.PrivateCFETSSpotClient("https://1navs.com", "secret")
        with mock.patch.object(uploader, "open_server_request", return_value=FakeResponse()) as opened:
            usd, jpy = client.fetch_many(("USD/CNY", "JPY/CNY"), date(2026, 8, 26))
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(opened.call_args.args[0].full_url, "https://1navs.com/api/v1/private/hk-connect-fx")
        self.assertEqual(usd.to_payload()["source"], "CFETS_SPOT_RATE")
        self.assertAlmostEqual(usd.rate, 6.72085)
        self.assertEqual(usd.fetched_at.microsecond, 435945)
        self.assertAlmostEqual(jpy.rate, 0.0422565)
        self.assertEqual(jpy.pair, "JPY/CNY")

    def test_gzip_json_upload_body_and_headers(self) -> None:
        payload = {"symbol": "SZ159518", "pcf": {"trading_day": "2026-07-10"}}
        encoded = uploader.gzip_json_body(payload)
        self.assertEqual(json.loads(gzip.decompress(encoded)), payload)
        headers = uploader.gzip_server_headers("https://1navs.com", "secret")
        self.assertEqual(headers["Content-Encoding"], "gzip")
        self.assertEqual(headers["X-Upload-Token"], "secret")

    def test_collection_windows_follow_shanghai_schedule(self) -> None:
        weekday = datetime(2026, 7, 10, 8, 30, tzinfo=SHANGHAI)
        self.assertTrue(uploader.pcf_collection_window(weekday))
        self.assertFalse(uploader.ib_collection_window(weekday))
        self.assertTrue(
            uploader.ib_collection_window(datetime(2026, 7, 10, 9, 0, tzinfo=SHANGHAI))
        )
        self.assertTrue(
            uploader.ib_collection_window(datetime(2026, 7, 10, 14, 59, 59, tzinfo=SHANGHAI))
        )
        self.assertFalse(
            uploader.ib_collection_window(datetime(2026, 7, 10, 15, 0, tzinfo=SHANGHAI))
        )
        self.assertFalse(
            uploader.pcf_collection_window(datetime(2026, 7, 11, 8, 30, tzinfo=SHANGHAI))
        )

    def test_parse_clock_minute_rejects_invalid_value(self) -> None:
        self.assertEqual(uploader.parse_clock_minute("08:30", 0), 8 * 60 + 30)
        self.assertEqual(uploader.parse_clock_minute("25:00", 123), 123)

    def test_ib_poll_preserves_ticker_network_time_instead_of_poll_time(self) -> None:
        class FakeIB:
            def isConnected(self) -> bool:
                return True

            def sleep(self, _seconds: float) -> None:
                return None

        class FakeTicker:
            bid = 158.61
            ask = 159.80
            last = 159.40
            marketDataType = 1
            time = datetime(2026, 7, 10, 1, 59, 58, tzinfo=timezone.utc)

        stream = uploader.IBQuoteStream("127.0.0.1", 7496, 15918, 1.0)
        stream.ib = FakeIB()
        stream.ticker = FakeTicker()

        quote = stream.poll(0)

        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertEqual(
            quote.observed_at,
            datetime(2026, 7, 10, 9, 59, 58, tzinfo=SHANGHAI),
        )
        self.assertIsNotNone(quote.stream_checked_at)

    def test_batch_upload_is_gzipped_and_requires_matching_acknowledgement(self) -> None:
        class FakeResponse:
            def __init__(self, body: dict[str, object]) -> None:
                self.body = json.dumps(body).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def read(self) -> bytes:
                return self.body

        response = FakeResponse({
            "ok": True,
            "batch_id": "xop-1",
            "accepted": ["SZ159518"],
            "rejected": {},
        })
        with mock.patch.object(uploader, "open_server_request", return_value=response) as opened:
            reply = uploader.post_private_input_batch(
                "https://1navs.com",
                "secret",
                [{"symbol": "SZ159518", "schema_version": 1}],
                12.0,
                source="xop-family",
                generated_at=datetime(2026, 7, 10, 10, 0, tzinfo=SHANGHAI),
                batch_id="xop-1",
            )
        self.assertEqual(reply["accepted"], ["SZ159518"])
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "https://1navs.com/api/v1/private/inputs/batch")
        self.assertEqual(request.get_header("Content-encoding"), "gzip")
        decoded = json.loads(gzip.decompress(request.data))
        self.assertEqual(decoded["batch_id"], "xop-1")
        self.assertEqual(decoded["inputs"][0]["symbol"], "SZ159518")

        wrong = FakeResponse({
            "ok": True,
            "batch_id": "other",
            "accepted": ["SZ159518"],
            "rejected": {},
        })
        with mock.patch.object(uploader, "open_server_request", return_value=wrong):
            with self.assertRaisesRegex(RuntimeError, "wrong batch_id"):
                uploader.post_private_input_batch(
                    "https://1navs.com",
                    "secret",
                    [{"symbol": "SZ159518"}],
                    12.0,
                    source="xop-family",
                    batch_id="xop-1",
                )

        partial = FakeResponse({
            "ok": False,
            "batch_id": "xop-1",
            "accepted": ["SZ159518"],
            "rejected": {"SH513350": "invalid"},
        })
        with mock.patch.object(uploader, "open_server_request", return_value=partial):
            reply = uploader.post_private_input_batch(
                "https://1navs.com",
                "secret",
                [{"symbol": "SZ159518"}, {"symbol": "SH513350"}],
                12.0,
                source="xop-family",
                batch_id="xop-1",
            )
        self.assertEqual(reply["rejected"], {"SH513350": "invalid"})

    def test_pcf_parser_uses_only_estimate_cash_and_counts_security_basket(self) -> None:
        day = date(2026, 7, 10)
        parsed = uploader.parse_pcf_xml(
            pcf_xml(),
            uploader.pcf_url_for_day(day),
            expected_trading_day=day,
        )
        self.assertEqual(parsed.security_id, "159518")
        self.assertEqual(parsed.estimate_cash_component_cny, 1284.61)
        self.assertEqual(parsed.component_count, 1)
        self.assertNotEqual(parsed.estimate_cash_component_cny, 999999.99)
        self.assertEqual(len(parsed.sha256), 64)

    def test_missing_estimate_cash_fails_closed_even_when_other_cash_fields_exist(self) -> None:
        with self.assertRaisesRegex(
            uploader.InputValidationError,
            "EstimateCashComponent",
        ):
            uploader.parse_pcf_xml(
                pcf_xml(include_estimate=False),
                uploader.pcf_url_for_day(date(2026, 7, 10)),
                expected_trading_day=date(2026, 7, 10),
            )

    def test_cfets_parser_selects_latest_published_hour_only(self) -> None:
        fetched_at = datetime(2026, 7, 10, 14, 1, tzinfo=SHANGHAI)
        quote = uploader.parse_cfets_response(
            {
                "records": [
                    {
                        "dealDate": "2026-07-10",
                        "ccyPair": "USD/CNY",
                        "rateOf10hour": "6.7801",
                        "rateOf11hour": "6.7788",
                        "rateOf14hour": "6.7765",
                        "rateOf15hour": "---",
                        "rateOf18hour": "",
                    }
                ]
            },
            date(2026, 7, 10),
            fetched_at,
        )
        self.assertIsNotNone(quote)
        assert quote is not None
        self.assertEqual(quote.quote_time, "14:00")
        self.assertEqual(quote.rate, 6.7765)
        self.assertEqual(quote.fetched_at, fetched_at)

    def test_payload_matches_backend_contract_and_preserves_raw_ib_sides(self) -> None:
        now = datetime(2026, 7, 10, 10, 0, 5, tzinfo=SHANGHAI)
        pcf = uploader.parse_pcf_xml(
            pcf_xml(),
            uploader.pcf_url_for_day(date(2026, 7, 10)),
            expected_trading_day=date(2026, 7, 10),
        )
        fx = uploader.CFETSQuote(6.7765, date(2026, 7, 10), "10:00", now)
        ib = uploader.IBQuote(158.61, 159.80, 159.40, "Live", now)
        payload = uploader.build_private_payload(pcf, fx, ib, generated_at=now)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["symbol"], "SZ159518")
        self.assertEqual(
            payload["model_version"],
            "private.total-basket.xop-cfets-pcf.v1",
        )
        self.assertEqual(payload["ib"]["bid"], 158.61)
        self.assertEqual(payload["ib"]["ask"], 159.80)
        self.assertEqual(payload["fx"]["source"], "CFETS_REFERENCE_RATE")
        self.assertEqual(payload["pcf"]["component_count"], 1)

    def test_spot_quote_accepts_real_minute_without_loosening_hourly_quote(self) -> None:
        now = datetime(2026, 8, 26, 10, 34, tzinfo=SHANGHAI)
        spot = uploader.CFETSSpotQuote(
            "USD/CNY", 6.72125, now.date(), "10:34", now,
        )
        uploader.validate_cfets_quote(spot)
        self.assertEqual(spot.to_payload()["quote_time"], "10:34")
        with self.assertRaisesRegex(uploader.InputValidationError, "hourly"):
            uploader.validate_cfets_quote(
                uploader.CFETSQuote(6.72125, now.date(), "10:34", now)
            )

    def test_calculator_realtime_seed_never_promotes_cash_number_to_strict_pcf(self) -> None:
        now = "2026-07-10T18:46:35.108+08:00"
        payload = {
            "schema_version": 2,
            "generated_at": now,
            "trade_date": "20260710",
            "symbol": "SZ159518",
            "valuation": {
                "xop": {
                    "symbol": "XOP",
                    "bid": 158.61,
                    "ask": 159.80,
                    "last": 159.40,
                    "market_data_type": "Live",
                    "received_at": now,
                },
                "fx": {
                    "source": "CFETS",
                    "pair": "USD/CNY",
                    "rate": 6.7765,
                    "trading_day": "2026-07-10",
                    "quote_time": "18:00",
                },
                "pcf": {
                    "estimate_cash_component_cny": 1284.61,
                    "trading_day": "2026-07-10",
                },
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "realtime.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            seed = uploader.load_input_file(path)
        self.assertIsNone(seed.pcf)
        self.assertEqual(seed.fx.rate, 6.7765)
        self.assertEqual(seed.ib.bid, 158.61)
        self.assertEqual(seed.ib.ask, 159.80)


if __name__ == "__main__":
    unittest.main()
